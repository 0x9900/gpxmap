#! /usr/bin/env python3
# vim:fenc=utf-8
#
# Copyright © 2026 fred <github-fred@hidzz.com>
#
# Distributed under terms of the BSD 3-Clause license.
# pylint: disable=too-many-locals

import logging
from string import Template

import matplotlib as mpl
import matplotlib.colors as mcolors
import numpy as np
from simplekml import AltitudeMode, Color, GxAltitudeMode, Kml

POINTS_SPACING = 10

DESCRIPTION = Template("""
UTC Time: $utc_time
Local Time: $time
Duration: $cumulative_duration minute

Altitude: $elevation_str m ($alt_feet feet)
Speed: $speed_kmh km/h ($speed_knot knot)
Distance: $cumulative_distance km
""").safe_substitute

README = Template("""
<![CDATA[<h2>GPX Map</h2>
<p>$title</p>
<p>For more information on GPX Map you can read
   <a href="https://gpx.bsdworld.org/">gpx.bsdworld.org</a>
</p> ]]>
""").safe_substitute

README_FLY = Template("""
<![CDATA[<h2>GPX Map</h2>
<p>$title</p>
<p>Elevation Max $max, Min: $min</p>
<p>For more information on GPX Map you can read
   <a href="https://gpx.bsdworld.org/">gpx.bsdworld.org</a>
</p> ]]>
""").safe_substitute


def kml_readme(kml, name, alt):
  folder = kml.newfolder(name='ReadMe')
  folder.snippet.maxlines = 0

  if all(v is not None for v in alt.values()):
    folder.description = README_FLY(title=name, max=int(alt['max']), min=int(alt['min']))
  else:
    folder.description = README(title=name)


def kml_points(kml, points, spacing, include_altitude=False):
  """spacing: minimum distance in km between consecutive kept points."""
  if spacing <= 0:
    raise ValueError("spacing must be a positive distance in km")

  data = points[['latitude', 'longitude', 'elevation', 'speed',
                 'cumulative_distance', 'cumulative_duration',
                 'time']].copy()
  data['cumulative_distance'] = data['cumulative_distance'] / 1000
  data['cumulative_duration'] = data['cumulative_duration'] / 60

  data = data.reset_index(drop=True)
  if not include_altitude:
    data['elevation'] = np.nan

  data['utc_time'] = (
    data['time'].dt.tz_localize('UTC') if data['time'].dt.tz is None
    else data['time'].dt.tz_convert('UTC')
  )

  # first point of every `spacing`-km bucket along the track
  bucket = np.floor(data['cumulative_distance'].to_numpy() / spacing).astype(int)
  is_bucket_start = np.concatenate(([True], np.diff(bucket) != 0))

  max_alt = data['elevation'].max()
  is_max = (data['elevation'] == max_alt).to_numpy()

  keep = is_bucket_start | is_max
  keep[0] = True
  keep[-1] = True

  sample = data.loc[keep]
  sample['alt_feet'] = sample['elevation'] * 3.280839895
  sample['speed_kmh'] = sample['speed'] * 3.6
  sample['speed_knot'] = sample['speed'] * 1.94384

  sample['elevation_str'] = np.char.mod('%.2f', sample['elevation'].fillna(0))
  sample['alt_feet'] = np.char.mod('%.2f', sample['alt_feet'].fillna(0))
  sample['speed_kmh'] = np.char.mod('%.2f', sample['speed_kmh'].fillna(0))
  sample['speed_knot'] = np.char.mod('%.2f', sample['speed_knot'].fillna(0))
  sample['cumulative_distance'] = np.char.mod('%.2f', sample['cumulative_distance'].fillna(0))
  sample['cumulative_duration'] = np.char.mod('%.0f', sample['cumulative_duration'].fillna(0))

  folder = kml.newfolder(name="Points", open=0)

  for idx, row in sample.iterrows():
    coords = (row['longitude'], row['latitude'], row['elevation'])
    kml_pnt = folder.newpoint(
      name=f"Point: #{idx}",
      coords=[coords],
      gxaltitudemode=GxAltitudeMode.relativetoseafloor,
    )
    kml_pnt.description = DESCRIPTION(row)
    kml_pnt.style.iconstyle.icon.href = (
      'https://bsdworld.org/marker-r.png' if row['elevation'] == max_alt
      else 'https://bsdworld.org/marker-b.png'
    )
    kml_pnt.style.labelstyle.scale = 0.75
    kml_pnt.style.iconstyle.color = 'ffffff00'
    kml_pnt.style.iconstyle.scale = 1.25


def kml_alt_line(kml, points, width=6.0):
  folder = kml.newfolder(name="Altitude line", open=0)

  norm = mcolors.Normalize(vmin=points["elevation"].min(), vmax=points["elevation"].max())
  cmap = mpl.colormaps["plasma"]

  coords = points[["longitude", "latitude", "elevation"]].values.tolist()
  elevations = points["elevation"].values

  for i in range(len(coords) - 1):
    seg = folder.newlinestring(name=f"altitude_seg{i}")
    seg.coords = [coords[i], coords[i + 1]]
    seg.altitudemode = AltitudeMode.relativetoground
    seg.extrude = 1
    seg.tessellate = 1

    r, g, b, _ = cmap(norm(elevations[i]))
    kml_color = Color.rgb(int(r * 255), int(g * 255), int(b * 255))
    seg.style.linestyle.color = kml_color
    seg.style.linestyle.width = width
    seg.style.polystyle.color = Color.changealphaint(180, kml_color)
    seg.style.polystyle.outline = 0


def kml_trek_line(kml, points, name, width=6.0, flat_threshold=0.1):
  folder = kml.newfolder(name="Route", open=0)
  points = points.reset_index(drop=True)

  if points['elevation'].isna().all():
    line = folder.newlinestring(name=name)
    line.coords = points[['longitude', 'latitude']].values.tolist()
    line.style.linestyle.color = Color.white
    line.style.linestyle.width = width
    return folder

  def trend(d):
    if d > flat_threshold:
      return 'up'
    if d < -flat_threshold:
      return 'down'
    return 'flat'

  alt_diff = points['elevation'].diff().fillna(0)
  trends = alt_diff.apply(trend)

  colors = {
    'up': Color.red,
    'down': Color.blue,
    'flat': Color.greenyellow,
  }

  group_id = (trends != trends.shift()).cumsum()
  # (start_pos, end_pos) for each contiguous run, in row-position space
  bounds = points.groupby(group_id).apply(lambda g: (g.index[0], g.index[-1]))
  logging.info('Number of segments: %d', bounds.shape[0])

  prev_end = None
  for start, end in bounds:
    seg_start = start if prev_end is None else prev_end  # share boundary point
    seg = points.iloc[seg_start:end + 1]
    prev_end = end

    if len(seg) < 2:
      continue

    seg_trend = trends.iloc[end]
    line = folder.newlinestring(name=f"{name} ({seg_trend})")
    line.coords = seg[['longitude', 'latitude']].values.tolist()
    line.style.linestyle.color = colors[seg_trend]
    line.style.linestyle.width = width

  return folder


def kml_sail_line(kml, points, name, width=6.0):
  folder = kml.newfolder(name="Route", open=0)

  sectors = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
  colors = {
    'N':  Color.red,
    'NE': Color.orange,
    'E':  Color.yellow,
    'SE': Color.green,
    'S':  Color.cyan,
    'SW': Color.blue,
    'W':  Color.purple,
    'NW': Color.magenta,
  }

  def bearing_to_sector(b):
    idx = int(((b % 360) + 22.5) // 45) % 8
    return sectors[idx]

  sector_series = points['bearing'].apply(bearing_to_sector)
  group_id = (sector_series != sector_series.shift()).cumsum()

  for _, seg in points.groupby(group_id):
    idx = seg.index
    start_pos = points.index.get_loc(idx[0])
    # extend backward one point so segments connect visually
    if start_pos > 0:
      start = points.index[start_pos - 1]
      seg = points.loc[start:idx[-1]]

    if len(seg) < 2:
      continue

    seg_sector = sector_series.loc[idx[-1]]
    line = folder.newlinestring(name=f"{name} ({seg_sector})")
    line.coords = seg[['longitude', 'latitude']].values.tolist()
    line.style.linestyle.color = colors[seg_sector]
    line.style.linestyle.width = width


def export_kml(gpx, filename, trip_type='trek', spacing=POINTS_SPACING):
  suffix = filename.suffix
  name = filename.stem

  points = gpx.points.copy()

  if suffix not in ('.kml', '.kmz'):
    raise IOError('Invalid file name: the suffix should be ".kml", or ".kmz"')

  kml = Kml(name=f"{name}", open=1)
  kml_readme(kml, name, gpx.elevations)
  if trip_type == 'fly' and all(v is not None for v in gpx.elevations):
    trip_type = 'trek'
    logging.warning('The fly trak is missing elevations')

  match trip_type:
    case 'fly':
      kml_alt_line(kml, points, name)
      kml_points(kml, points, spacing, True)
    case 'sail':
      kml_sail_line(kml, points, name)
      kml_points(kml, points, spacing, False)
    case _:
      kml_trek_line(kml, points, name)
      kml_points(kml, points, spacing, False)

  if suffix == '.kmz':
    kml.savekmz(filename)
  else:
    kml.save(filename)

  logging.info('File %s saved', filename)
