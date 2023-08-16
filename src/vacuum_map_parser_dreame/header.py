"""Dreame map header."""

from dataclasses import dataclass

from vacuum_map_parser_base.map_data import Point


@dataclass
class MapDataHeader:
    """Dreame map header."""

    map_index: int
    frame_type: int
    vacuum_position: Point
    charger_position: Point
    image_pixel_size: int
    image_width: int
    image_height: int
    image_left: int
    image_top: int
