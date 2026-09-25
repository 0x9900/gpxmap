#
# Copyright © 2024-2026 fred <github-fred@hidzz.com>
#
# Distributed under terms of the BSD 3-Clause license.



from .gpxmap import (DataError, MapPoints, draw_bearings, draw_points,
                     draw_routes, draw_sail, draw_tacks, draw_waypoints,
                     export_kml, fly_stats, read_gpx, sail_stats, trek_stats,
                     type_time)

__version__ = '0.1.6'

__all__ = ["DataError", "MapPoints", "draw_bearings", "draw_points",
           "draw_routes", "draw_sail", "draw_tacks", "draw_waypoints",
           "export_kml", "fly_stats", "read_gpx", "sail_stats", "trek_stats",
           "type_time"]
