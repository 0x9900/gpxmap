# GPX Map

## Example:

[GPX Maps examples](https://gpx.bsdworld.org/)

## Usage:

```
usage: gpxmap [-h] [--start START] [--end END] [-w WAY_POINTS] [-o OUTPUT]
              [-O] [-K] [-t {trek,sail,fly}]
              filename

positional arguments:
  filename

options:
  -h, --help            show this help message and exit
  --start START         number of points to cut at the beginning.
  --end END             number of points to cut at the end.
  -w WAY_POINTS, --way-points WAY_POINTS
                        Waypoints and markers
  -o OUTPUT, --output OUTPUT
                        Output filename
  -O, --open            Open the map in your browser
  -K, --kml             Generate a KML file
  -t {trek,sail,fly}, --type {trek,sail,fly}
                        Trace type
```
