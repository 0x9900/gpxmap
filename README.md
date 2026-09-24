
# GPX Map

**GPX Map** is a Python command-line tool that turns a GPX track into an interactive HTML map with statistics and graphs.

It can also generate a KML file for use with applications such as Google Earth.

![GPX Map example](docs/example.png)

## Features

    * Interactive map of the GPX track
    * Track statistics and graphs
    * Waypoints and markers
    * Trim unwanted points from a track
    * Support for trek, sailing and flying tracks
    * Optional KML export
    * Generated HTML can be opened directly in a browser

## Installation

```bash
pip install gpxmap
```

Or install the latest development version:

```bash
git clone https://github.com/0x9900/gpxmap.git
cd gpxmap
pip install .
```

## Usage

```bash
$> gpxmap --output /tmp/track.html track.gpx
2026-09-24 15:21:05 INFO 660:  Trace type: trek
2026-09-24 15:21:07 INFO 220:  Total number of points: 5208
2026-09-24 15:21:07 INFO 227:  Displayed points: 5204
2026-09-24 15:21:07 INFO 292:  Save /tmp/track.png
2026-09-24 15:21:08 INFO 707:  Writing: /tmp/track.html
```

This generates an HTML file containing the map and track information.

To also generate a KML file:

```bash
$> gpxmap --output /tmp/track.html track.gpx --kml
2026-09-24 15:22:23 INFO 660:  Trace type: trek
2026-09-24 15:22:24 INFO 220:  Total number of points: 5208
2026-09-24 15:22:24 INFO 227:  Displayed points: 5204
2026-09-24 15:22:25 INFO 292:  Save /tmp/track.png
2026-09-24 15:22:25 INFO 707:  Writing: /tmp/track.html
2026-09-24 15:22:25 INFO 173:  Number of segments: 1278
2026-09-24 15:22:26 INFO 264:  File /tmp/track.kmz saved
```

See `gpxmap --help` for all available options.

## Examples

Generated examples are available at:

**https://gpx.bsdworld.org/**

## License

GPX Map is released under the [BSD 3-Clause License](LICENSE).
