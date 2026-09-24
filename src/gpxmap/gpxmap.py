#! /usr/bin/env python
# vim:fenc=utf-8
#
# Copyright © 2024 fred <github-fred@hidzz.com>
#
# Distributed under terms of the BSD 3-Clause license.

"""
Send my GPS traces on a map.
Examples: https://gpx.bsdworld.org/
"""
import argparse
import logging
import math
import pathlib
import webbrowser
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

import branca
import folium
import gpxpy
import gpxpy.gpx
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyproj
from folium.plugins import PolyLineTextPath
from gpxpy.gpx import GPX, GPXRoute, GPXTrackPoint, GPXWaypoint
from matplotlib import gridspec, ticker
from pandas.core.series import Series
from tzlocal import get_localzone

from .kmltool import export_kml
from .textbox import add_infobox

MAX_POINTS = 100_000

logging.basicConfig(format='%(asctime)s %(levelname)s %(lineno)d:  %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.INFO)

MS2KMH = 3.6
MS2KNOTS = 1.9438444924574

DPI = 100
LIGHTGRAY = "#303030"
FACECOLOR = "white"
EDGECOLOR = "#8b8bab"

MY_PARAMS = {
  'axes.edgecolor': EDGECOLOR,
  'axes.facecolor': FACECOLOR,
  'axes.grid': True,
  'axes.grid.which': 'both',
  'axes.labelcolor': LIGHTGRAY,
  'axes.labelsize': 8,
  'axes.linewidth': 1.5,
  'axes.spines.bottom': True,
  'axes.spines.left': True,
  'axes.spines.right': False,
  'axes.spines.top': False,
  'axes.titlesize': 10,
  'figure.dpi': 100,
  'figure.edgecolor': FACECOLOR,
  'figure.facecolor': FACECOLOR,
  'font.size': 10,
  'grid.alpha': 0.7,
  'grid.color': LIGHTGRAY,
  'grid.linestyle': 'dashed',
  'grid.linewidth': 0.25,
  'text.color': LIGHTGRAY,
  'xtick.color': LIGHTGRAY,
  'xtick.labelcolor': LIGHTGRAY,
  'xtick.labelsize': 6,
  'xtick.minor.visible': False,
  'ytick.color': LIGHTGRAY,
  'ytick.labelcolor': LIGHTGRAY,
  'ytick.labelsize': 6,
  'ytick.minor.visible': False
}


@dataclass(frozen=True, slots=True)
class MapPoints:
  elevations: dict
  points: pd.DataFrame
  waypoints: list
  routes: list


class DataError(Exception):
  pass


def remove_outliers(points: pd.DataFrame) -> pd.DataFrame:
  if points.empty:
    return points

  q1_speed = points['speed'].quantile(0.25)
  q3_speed = points['speed'].quantile(0.75)
  iqr_speed = q3_speed - q1_speed

  filter_speed = (
    (points['speed'] >= q1_speed - 1.5 * iqr_speed) &
    (points['speed'] <= q3_speed + 1.5 * iqr_speed)
  )
  return points.loc[filter_speed]


def find_farthest_point(points: pd.DataFrame) -> Series:
  geod = pyproj.Geod(ellps='WGS84')
  orig = np.full((points.shape[0], 2), tuple(points.iloc[0][['longitude', 'latitude']]))
  points['max_distance'] = geod.inv(
    orig[:, 0], orig[:, 1], points.longitude.values, points.latitude.values
  )[2]
  index = points[points.max_distance == points.max_distance.max()].index[0]
  return points.iloc[index]


def select_time(points: pd.DataFrame,
                start_time: time | None,
                end_time: time | None) -> pd.DataFrame:
  start = end = None

  if start_time:
    start = points.iloc[0].time.replace(hour=start_time.hour, minute=start_time.minute,
                                        second=start_time.second, microsecond=0)
    points = points[start <= points.time]
  if end_time:
    end = points.iloc[-1].time.replace(hour=end_time.hour, minute=end_time.minute,
                                       second=end_time.second, microsecond=0)
    points = points[points.time <= end]

  return points


def extract_points(gpx_data: GPX) -> pd.DataFrame:
  if not gpx_data.tracks:
    return pd.DataFrame([])

  pts = []
  for track in gpx_data.tracks:
    for segment in track.segments:
      if duration := segment.get_duration():
        segment.reduce_points(16 if duration < 86400 else 64)
      for idx, point in enumerate(segment.points):
        _time = point.time
        if _time and _time.tzname() is None:
          _time = _time.replace(tzinfo=ZoneInfo('UTC'))
        speed = segment.get_speed(idx) or 0.0
        pts.append({
          'time': _time,
          'latitude': point.latitude,
          'longitude': point.longitude,
          'elevation': point.elevation,
          'speed': speed,
        })
  return pd.DataFrame(pts)


def calc_distance(points):
  geod = pyproj.Geod(ellps='WGS84')
  points['prev_lat'] = points['latitude'].shift()
  points['prev_lon'] = points['longitude'].shift()
  distance = np.zeros(len(points))
  distance = np.array(geod.inv(points['longitude'].values, points['latitude'].values,
                      points['prev_lon'].values, points['prev_lat'].values))
  distance[np.isnan(distance)] = 0
  points[['bearing', 'inv', 'distance']] = np.transpose(distance)
  points = points.drop(columns=['prev_lat', 'prev_lon'])
  return points


def process_tracks(gpx_data: GPX,
                   start: time | None,
                   end: time | None,
                   localtz: ZoneInfo) -> pd.DataFrame:
  points = extract_points(gpx_data)
  if points.empty:
    return points

  if gpx_data.has_times():
    points.time = points.time.dt.tz_convert(localtz)
    try:
      points = select_time(points, start, end)
      points = points.reset_index(drop=True)
    except IndexError:
      raise DataError('The selected times are out-of-bounds') from None
    points['cumulative_duration'] = points.time.diff().dt.total_seconds().fillna(0).cumsum()
  else:
    # No timestamps available: signal "missing" explicitly
    points['time'] = pd.NaT
    points['time'] = pd.to_datetime(points['time'], utc=True)
    points['cumulative_duration'] = 0.0

  points = calc_distance(points)
  points['cumulative_distance'] = points.distance.cumsum()
  return points


def read_gpx(filename: pathlib.Path,
             start: time | None = None,
             end: time | None = None,
             localtz: ZoneInfo = ZoneInfo('UTC')) -> MapPoints:
  try:
    with filename.open('r', encoding='utf-8', errors='replace') as gpx_fd:
      gpx_data = gpxpy.parse(gpx_fd)
  except (gpxpy.gpx.GPXException, gpxpy.gpx.GPXXMLSyntaxException, ValueError) as err:
    raise DataError(f'This is not a .gpx file: {err}') from None

  # if not gpx_data.has_times():
  #   raise DataError('This gpx file does not contain any time data')
  gpx_data.smooth()
  elevations = dict(zip(['min', 'max'], gpx_data.get_elevation_extremes()))

  points = process_tracks(gpx_data, start, end, localtz)
  nb_points = points.shape[0]
  logging.info('Total number of points: %d', nb_points)
  if nb_points > MAX_POINTS:
    increment = int(np.ceil(points.shape[0] / MAX_POINTS))
    points = points.iloc[::increment]

  points = remove_outliers(points)
  points = points.reset_index(drop=True)
  logging.info('Displayed points: %d', points.shape[0])
  return MapPoints(elevations, points, gpx_data.waypoints, gpx_data.routes)


def calculate_heading(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  delta_lon = lon2 - lon1
  x = math.sin(math.radians(delta_lon)) * math.cos(math.radians(lat2))
  y = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) -
       math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) *
       math.cos(math.radians(delta_lon)))
  heading = math.degrees(math.atan2(x, y))
  return (heading + 360) % 360


def seconds_to_hms(sec, _):
  if sec == 0:
    return 'Start'
  hour = int(sec // 3600)
  minute = int((sec % 3600) // 60)
  return f'{hour:02d}:{minute:02d}'


def trek_stats(image_name: pathlib.Path, points: pd.DataFrame) -> None:
  if points.empty:
    return

  speed = points.speed.values
  speed = points.speed * MS2KMH
  elevation = points['elevation'].fillna(0).round(1)
  cumulative_duration = points.cumulative_duration.values

  plt.rcParams.update(MY_PARAMS)
  fig = plt.figure(figsize=(9, 2.5))
  gs = gridspec.GridSpec(1, 5, figure=fig)
  ax1 = fig.add_subplot(gs[0, 0])
  ax1.boxplot(
    speed, patch_artist=True, showcaps=True,
    boxprops={'color': '#9C9DED', 'linewidth': 1},
    whiskerprops={'color': '#006BA8'},
    capprops={'color': '#9C9DED'},
    flierprops={'markerfacecolor': '#006BA8', 'markeredgecolor': 'none', 'markersize': 3},
  )
  ax1.set_title('Average speed')
  ax1.get_xaxis().set_ticks([])
  ax1.set_ylabel('km/h')

  ax2 = fig.add_subplot(gs[0, 1:3 if elevation.any() else 5])
  ax2.set_title('Speed')
  ax2.scatter(cumulative_duration, speed, s=.2)
  ax2.axhline(np.percentile(speed, 75), linewidth=1, color='orange')
  ax2.set_ylim(bottom=0)
  formatter = ticker.FuncFormatter(seconds_to_hms)
  ax2.xaxis.set_major_formatter(formatter)
  ax2.set_xlabel('Time in HH:MM')

  if elevation.any():
    ax3 = fig.add_subplot(gs[0, 3:5])
    ax3.set_title('Elevation')
    ax3.plot(cumulative_duration, elevation)
    ax3.set_ylim(bottom=0)
    ax3.xaxis.set_major_formatter(formatter)
    ax3.set_xlabel('Time in HH:MM')
    ax3.set_ylabel('Metres')

  plt.tight_layout()
  logging.info('Save %s', image_name)
  plt.savefig(image_name, bbox_inches='tight')


def fly_stats(image_name: pathlib.Path, points: pd.DataFrame) -> None:
  if points.empty:
    return

  speed_knots = points.speed.values
  speed_knots = speed_knots * MS2KNOTS
  cumulative_duration = points.cumulative_duration.values
  bearing = np.deg2rad(points.inv.values).round(3)

  plt.rcParams.update(MY_PARAMS)
  fig = plt.figure(figsize=(9, 2.5))
  gs = gridspec.GridSpec(1, 4, figure=fig)

  ax1 = fig.add_subplot(gs[0, 0:2])  # Occupies 1 column
  ax1.set_title('Speed')
  ax1.scatter(cumulative_duration, speed_knots, s=.2)
  ax1.axhline(np.percentile(speed_knots, 75), linewidth=1, color='orange')
  ax1.set_ylim(bottom=0)
  formatter = ticker.FuncFormatter(seconds_to_hms)
  ax1.xaxis.set_major_formatter(formatter)
  ax1.set_xlabel('Time in HH:MM')

  ax2 = fig.add_subplot(gs[0, 2], polar=True)
  ax2.spines['polar'].set_visible(False)
  ax2.set_rlabel_position(320)
  ax2.set_theta_zero_location("N")
  ax2.set_theta_direction(-1)
  ax2.scatter(bearing, speed_knots, s=.5)

  ax3 = fig.add_subplot(gs[0, 3])
  ax3.set_title('Elevation')
  ax3.plot(cumulative_duration, points.elevation)
  ax3.set_ylim(bottom=0)
  ax3.xaxis.set_major_formatter(formatter)
  ax3.set_xlabel('Time in HH:MM')
  ax3.set_ylabel('Metres')

  plt.tight_layout()
  logging.info('Save %s', image_name)
  plt.savefig(image_name, bbox_inches='tight')


def sail_stats(image_name: pathlib.Path, points: pd.DataFrame) -> None:
  if points.empty:
    return

  speed_knots = points.speed.values
  speed_knots = speed_knots * MS2KNOTS
  cumulative_duration = points.cumulative_duration.values
  bearing = np.deg2rad(points.inv.values).round(3)

  plt.rcParams.update(MY_PARAMS)
  fig = plt.figure(figsize=(9, 2.5))
  gs = gridspec.GridSpec(1, 4, figure=fig)
  ax1 = fig.add_subplot(gs[0, 0])  # Occupies 1 column
  ax1.boxplot(
    speed_knots, patch_artist=True, showcaps=True,
    boxprops={'color': '#9C9DED', 'linewidth': 1},
    whiskerprops={'color': '#006BA8'},
    capprops={'color': '#9C9DED'},
    flierprops={'markerfacecolor': '#006BA8', 'markeredgecolor': 'none', 'markersize': 3},
  )
  ax1.set_title('Average Velocity')
  ax1.get_xaxis().set_ticks([])
  ax1.set_ylabel('Knots')

  ax2 = fig.add_subplot(gs[0, 1:3])  # Occupies 3 columns
  ax2.set_title('Velocity')
  ax2.scatter(cumulative_duration, speed_knots, s=.2)
  ax2.axhline(np.percentile(speed_knots, 75), linewidth=1, color='orange')
  ax2.set_ylim(bottom=0)
  formatter = ticker.FuncFormatter(seconds_to_hms)
  ax2.xaxis.set_major_formatter(formatter)
  ax2.set_xlabel('Time in HH:MM')

  ax3 = fig.add_subplot(gs[0, 3], polar=True)
  ax3.spines['polar'].set_visible(False)
  ax3.set_rlabel_position(320)
  ax3.set_theta_zero_location("N")
  ax3.set_theta_direction(-1)
  ax3.scatter(bearing, speed_knots, s=.5)

  plt.tight_layout()
  logging.info('Save %s', image_name)
  plt.savefig(image_name, bbox_inches='tight')


def detect_tacks(points: pd.DataFrame,
                 threshold_angle: int = 90,
                 sample_factor: int = 10) -> pd.DataFrame:
  last_heading = None
  cumulative_change = 0
  tack_in_progress = False

  tacks: list[GPXTrackPoint] = []
  for i in range(0, len(points.index) - sample_factor, sample_factor):
    pt1 = points.iloc[i]
    pt2 = points.iloc[i + sample_factor]
    heading = calculate_heading(pt1.latitude, pt1.longitude, pt2.latitude, pt2.longitude)

    if last_heading is not None:
      # Calculate the change in heading
      change = heading - last_heading
      if abs(change) > 180:
        change -= np.sign(change) * 360  # Normalize to -180 to 180
      cumulative_change += change

      # Check if the cumulative change indicates a tack
      if abs(cumulative_change) >= threshold_angle:
        if not tack_in_progress:
          tacks.append(points.iloc[i - 6])
          tack_in_progress = True
          cumulative_change = 0
        else:
          tack_in_progress = False
    last_heading = heading

  return pd.DataFrame(tacks)


def nbsp(var: str | None) -> str | None:
  if not var or not isinstance(var, str):
    return var
  return var.replace(' ', '&nbsp;')


def draw_bearings(chart: folium.Map, points: pd.DataFrame) -> None:
  if points.empty:
    return

  _html = '<div style="font-size: 8pt; color: #333333;">{}º</div>'
  layer = folium.FeatureGroup(name='Bearings')
  geod = pyproj.Geod(ellps='WGS84')

  nb_points = int(points.shape[0] / 20)
  try:
    selected = points.iloc[::nb_points, :]
  except ValueError:
    return
  for _, pnt in selected.iterrows():
    lat, lon, bearing = pnt[['latitude', 'longitude', 'inv']]
    dest_lon, dest_lat, _ = geod.fwd(lon, lat, bearing, 75)
    folium.CircleMarker([lat, lon], radius=3, color="#888888").add_to(layer)
    folium.PolyLine([[lat, lon], [dest_lat, dest_lon]], color='#888888').add_to(layer)
    html = _html.format(int((bearing + 360) % 360))
    folium.Marker([dest_lat, dest_lon], icon=folium.DivIcon(html=html)).add_to(layer)

  layer.add_to(chart)


def draw_tacks(chart: folium.Map, points: pd.DataFrame) -> None:
  if points.empty:
    return
  tacks = detect_tacks(points, threshold_angle=80, sample_factor=8)
  tacks = tacks.reset_index()
  if tacks.empty:
    return

  layer = folium.FeatureGroup(name='Tacks')
  try:
    for _, pnt in tacks.iterrows():
      icon = folium.Icon(color="blue", prefix='fa', icon="sailboat")
      popup = [f"<b>Distance:</b> {round(pnt.cumulative_distance / 1852, 2)}nm"]
      if pnt.time != pd.Timestamp(0):
        popup.append(f"<b>Time:</b> {pnt.time.strftime('%Y/%m/%d %H:%M:%S')}<br>")
      folium.Marker(location=(pnt.latitude, pnt.longitude),
                    popup=nbsp('\n'.join(popup)),
                    icon=icon).add_to(layer)
  except (AttributeError, TypeError) as err:
    logging.error(err)
  layer.add_to(chart)


def draw_points(chart: folium.Map, points: pd.DataFrame) -> None:
  if points.empty:
    return

  folium.PolyLine(points[['latitude', 'longitude']], color='green').add_to(chart)

  speed = points.speed.values * MS2KMH
  layer = folium.FeatureGroup(name='Velocity')

  try:
    start = points.iloc[0]
    if start.time is not pd.NaT and start.time != pd.Timestamp(0).tz_localize('UTC'):
      popup = nbsp(f"<b>Start:</b> {start.time.strftime('%Y/%m/%d %H:%M:%S')}")
      folium.Marker(
        location=(start.latitude, start.longitude),
        icon=folium.Icon(color='green'),
        popup=popup
      ).add_to(chart)

    finish = points.iloc[-1]
    popup = [f"<b>Distance:</b> {round(finish.cumulative_distance / 1000.0, 2)}km<br>"]
    if finish.cumulative_duration != 0.0:
      duration = datetime.fromtimestamp(finish.cumulative_duration).strftime("%H:%M:%S")
      popup.append(f"<b>Finish:</b> {finish.time.strftime('%Y/%m/%d %H:%M:%S')}<br>")
      popup.append(f"<b>Time:</b> {duration}")
      folium.Marker(location=(finish.latitude, finish.longitude), icon=folium.Icon(color='green'),
                    popup=nbsp('\n'.join(popup))).add_to(chart)
  except (AttributeError, TypeError) as err:
    logging.error(err)

  # colormap = getattr(branca.colormap.linear, 'YlOrRd_08').scale(
  #  np.floor(np.min(speed)),
  #  np.ceil(np.max(speed))).to_step(6)
  colormap = branca.colormap.LinearColormap(
    ["yellow", "green", "purple"], vmin=np.floor(np.min(speed)), vmax=np.ceil(np.max(speed))
  ).to_step(7)
  colormap.caption = 'Velocity (in km/h)'
  folium.ColorLine(positions=points[['latitude', 'longitude']], colormap=colormap,
                   weight=5, colors=speed).add_to(layer)
  chart.add_child(layer)
  colormap.add_to(chart)


def draw_sail(chart: folium.Map, points: pd.DataFrame) -> None:
  if points.empty:
    return

  farthest = find_farthest_point(points)
  folium.PolyLine(points[['latitude', 'longitude']]).add_to(chart)

  try:
    popup = [f"<b>Distance:</b> {round(farthest.cumulative_distance / 1852, 2)}nm<br>"]
    if farthest.cumulative_duration != pd.Timestamp(0):
      duration = datetime.fromtimestamp(farthest.cumulative_duration).strftime("%H:%M:%S")
      far_time = farthest.time.strftime('%Y/%m/%d %H:%M:%S')
      popup.append(f"<b>Time:</b> {far_time}<br>")
      popup.append(f"<b>Duration:</b> {duration}")
    folium.Marker(
      location=(farthest.latitude, farthest.longitude),
      tooltip="Click me!",
      icon=folium.Icon(color="red"),
      popup=nbsp('\n'.join(popup))
    ).add_to(chart)

    start = points.iloc[0]
    if start.time != pd.Timestamp(0):
      popup = nbsp(f"<b>Start:</b> {start.time.strftime('%Y/%m/%d %H:%M:%S')}")
    else:
      popup = '<b>Start</b>'
    folium.Marker(
      location=(start.latitude, start.longitude),
      icon=folium.Icon(color='green'),
      popup=popup
    ).add_to(chart)

    finish = points.iloc[-1]
    popup = [f"<b>Distance:</b> {round(finish.cumulative_distance / 1852, 2)}nm<br>"]
    if finish.cumulative_duration != pd.Timestamp(0):
      duration = datetime.fromtimestamp(finish.cumulative_duration).strftime("%H:%M:%S")
      popup.append(f"<b>Finish:</b> {finish.time.strftime('%Y/%m/%d %H:%M:%S')}<br>")
      popup.append(f"<b>Time:</b> {duration}")
      folium.Marker(location=(finish.latitude, finish.longitude), icon=folium.Icon(color='green'),
                    popup=nbsp('\n'.join(popup))).add_to(chart)
  except (AttributeError, TypeError) as err:
    logging.error(err)

  speed = points.speed.values * MS2KNOTS
  layer = folium.FeatureGroup(name='Velocity')
  colormap = getattr(branca.colormap.linear, 'YlOrRd_05').scale(
    np.floor(np.min(speed)),
    np.ceil(np.max(speed))).to_step(6)
  colormap.caption = 'Velocity (in knots)'
  folium.ColorLine(positions=points[['latitude', 'longitude']], colormap=colormap,
                   weight=5, colors=speed).add_to(layer)
  chart.add_child(layer)
  colormap.add_to(chart)


def draw_routes(chart: folium.Map, routes: list[GPXRoute]) -> None:
  attr = {"fill": "gray", "font-weight": "bold", "font-size": "12"}
  if not routes:
    return

  for rte in routes:
    points = []
    for point in rte.points:
      points.append((point.latitude, point.longitude))
    line = folium.PolyLine(points, color='gray')
    label = PolyLineTextPath(line, rte.name, offset=-10, attributes=attr)
    line.add_to(chart)
    label.add_to(chart)


def draw_waypoints(chart: folium.Map, wpts: list[GPXWaypoint]) -> None:
  if not wpts:
    return

  icon_html = """<div style="font-size: 16px; color: navy;">
  <i class="fa-solid fa-map-pin"></i>
  <span style="color: #333; font-size: 12px; font-weight: bold;
  position: absolute; margin: 7px 2px;">{}</span>
  </div>"""

  layer = folium.FeatureGroup(name='Markers')
  for pts in wpts:
    icon = folium.DivIcon(html=icon_html.format(nbsp(pts.name)))
    if pts.description:
      popup = folium.Popup(nbsp(pts.description), max_width=300)
    else:
      popup = None
    folium.Marker(location=(pts.latitude, pts.longitude), popup=popup,
                  icon=icon).add_to(layer)
  layer.add_to(chart)


def add_markers(chart: folium.Map, waypoints: list[GPXWaypoint]) -> None:
  icon_html = """
  <div style="font-size: 14px; color: olive;">
  <i class="fa fa-flag"></i>
  </div>"""

  layer = folium.FeatureGroup(name='Waypoints')
  for marker in waypoints:
    icon_only = folium.DivIcon(html=icon_html)
    folium.Marker(location=(marker.latitude, marker.longitude),
                  name=marker.name, popup=marker.comment,
                  icon=icon_only).add_to(layer)
  layer.add_to(chart)


def get_local_timezone() -> ZoneInfo:
  return get_localzone()


def type_time(value: str | None) -> time | None:
  if value is None:
    return None

  date_formats = ("%H:%M", "%H:%M:%S",)
  value = value.strip()
  for fmt in date_formats:
    try:
      _time = datetime.strptime(value, fmt).time()
      return _time
    except ValueError:
      continue
  raise argparse.ArgumentTypeError('Wrong time format')


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser()
  parser.add_argument('--start', type=type_time, default=None,
                      help="number of points to cut at the beginning.")
  parser.add_argument('--end', type=type_time, default=None,
                      help="number of points to cut at the end.")
  parser.add_argument('-w', '--way-points', type=pathlib.Path, default=None,
                      help='Waypoints and markers')
  parser.add_argument('filename', type=pathlib.Path, nargs=1)
  parser.add_argument('-o', '--output', type=pathlib.Path, default=None,
                      help='Output filename')
  parser.add_argument('-O', '--open', action="store_true", default=False,
                      help='Open the map in your browser')
  parser.add_argument('-K', '--kml', action="store_true", default=False,
                      help='Generate a KML file')
  parser.add_argument('-t', '--type', choices=['trek', 'sail', 'fly'], default='trek',
                      help='Trace type')
  return parser.parse_args()


def main() -> None:
  opts = parse_args()
  logging.info('Trace type: %s', opts.type)

  try:
    gpx_file = opts.filename[0].absolute()
    out_file = (opts.output or gpx_file.with_suffix('.html')).absolute()
    gpx_data = read_gpx(gpx_file, opts.start, opts.end, get_local_timezone())
  except (FileNotFoundError, DataError) as err:
    logging.error(err)
    raise SystemExit(err) from None

  try:
    if opts.way_points:
      with opts.way_points.open('r') as gpx_fd:
        markers = gpxpy.parse(gpx_fd).waypoints
    else:
      markers = []
  except FileNotFoundError as err:
    logging.error(err)
    raise SystemExit(err) from None

  chart = folium.Map()

  if opts.type == 'sail':
    folium.TileLayer(tiles='https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png',
                     attr='OpenSeaMap', name='OpenSeaMap', overlay=True,
                     control=True).add_to(chart)
    draw_sail(chart, gpx_data.points)
    draw_tacks(chart, gpx_data.points)
    draw_bearings(chart, gpx_data.points)
    sail_stats(out_file.with_suffix('.png'), gpx_data.points)
  elif opts.type == 'fly':
    folium.TileLayer(tiles='USGS.USTopo', overlay=True, control=True).add_to(chart)
    draw_points(chart, gpx_data.points)
    fly_stats(out_file.with_suffix('.png'), gpx_data.points)
  else:
    folium.TileLayer(tiles='USGS.USTopo', overlay=True, control=True).add_to(chart)
    draw_points(chart, gpx_data.points)
    trek_stats(out_file.with_suffix('.png'), gpx_data.points)

  draw_waypoints(chart, gpx_data.waypoints)
  draw_waypoints(chart, markers)
  draw_routes(chart, gpx_data.routes)
  folium.LayerControl().add_to(chart)

  chart.fit_bounds(chart.get_bounds(), padding=(10, 10))
  add_infobox(chart, out_file.stem)
  chart.save(out_file)
  logging.info('Writing: %s', out_file)
  if opts.open:
    webbrowser.open(out_file.as_uri())

  if opts.kml:
    kml_file = out_file.with_suffix('.kmz')
    export_kml(gpx_data, kml_file, opts.type)


if __name__ == '__main__':
  main()
