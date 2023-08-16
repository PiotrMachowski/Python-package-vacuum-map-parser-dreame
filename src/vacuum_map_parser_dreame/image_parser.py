"""Dreame map image parser."""

import logging
from enum import IntEnum
from typing import Any

from PIL import Image
from PIL.Image import Image as ImageType
from PIL.Image import Resampling
from vacuum_map_parser_base.config.color import ColorsPalette, SupportedColor
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.map_data import Room

from .header import MapDataHeader
from .map_data_type import MapDataType

_LOGGER = logging.getLogger(__name__)


class PixelTypes(IntEnum):
    """Dreame map pixel type."""

    NONE = 0
    FLOOR = 1
    WALL = 2


class DreameImageParser:
    """Dreame map image parser."""

    def __init__(self, palette: ColorsPalette, image_config: ImageConfig):
        self._palette = palette
        self._image_config = image_config

    def parse(
        self, raw_data: bytes, header: MapDataHeader, map_data_type: MapDataType
    ) -> tuple[ImageType | None, dict[int, Room]]:
        if (
            header.image_width is None
            or header.image_width == 0
            or header.image_height is None
            or header.image_height == 0
        ):
            return None, {}
        scale = self._image_config.scale
        trim_left = int(self._image_config.trim.left * header.image_width / 100)
        trim_right = int(self._image_config.trim.right * header.image_width / 100)
        trim_top = int(self._image_config.trim.top * header.image_height / 100)
        trim_bottom = int(self._image_config.trim.bottom * header.image_height / 100)
        trimmed_height = header.image_height - trim_top - trim_bottom
        trimmed_width = header.image_width - trim_left - trim_right
        image = Image.new("RGBA", (trimmed_width, trimmed_height))
        pixels = image.load()
        rooms: dict[int, Room] = {}

        for img_y in range(trimmed_height):
            for img_x in range(trimmed_width):
                x = img_x
                y = trimmed_height - img_y - 1
                room_x = img_x + trim_left
                room_y = img_y + trim_bottom

                if map_data_type == MapDataType.REGULAR:
                    px = raw_data[img_x + trim_left + header.image_width * (img_y + trim_bottom)]
                    segment_id = px >> 2
                    if 0 < segment_id < 62:
                        self._create_or_update_room(pixels, room_x, room_y, rooms, segment_id, x, y)
                    else:
                        masked_px = px & 0b00000011

                        if masked_px == PixelTypes.NONE.value:
                            pixels[x, y] = self._palette.get_color(SupportedColor.MAP_OUTSIDE)
                        elif masked_px == PixelTypes.FLOOR.value:
                            pixels[x, y] = self._palette.get_color(SupportedColor.MAP_INSIDE)
                        elif masked_px == PixelTypes.WALL.value:
                            pixels[x, y] = self._palette.get_color(SupportedColor.MAP_WALL)
                        else:
                            _LOGGER.warning("unhandled pixel type: %d", px)
                elif map_data_type == MapDataType.RISM:
                    px = raw_data[img_x + trim_left + header.image_width * (img_y + trim_bottom)]
                    segment_id = px & 0b01111111
                    wall_flag = px >> 7

                    if wall_flag:
                        pixels[x, y] = self._palette.get_color(SupportedColor.MAP_WALL)
                    elif segment_id > 0:
                        self._create_or_update_room(pixels, room_x, room_y, rooms, segment_id, x, y)

        if self._image_config.scale != 1 and header.image_width != 0 and header.image_height != 0:
            image = image.resize((int(trimmed_width * scale), int(trimmed_height * scale)), resample=Resampling.NEAREST)
        return image, rooms

    def _create_or_update_room(
        self, pixels: Any, room_x: int, room_y: int, rooms: dict[int, Room], segment_id: int, x: int, y: int
    ) -> None:
        if segment_id not in rooms:
            rooms[segment_id] = Room(room_x, room_y, room_x, room_y, segment_id)
        rooms[segment_id] = Room(
            min(rooms[segment_id].x0, room_x),
            min(rooms[segment_id].y0, room_y),
            max(rooms[segment_id].x1, room_x),
            max(rooms[segment_id].y1, room_y),
            segment_id,
        )
        pixels[x, y] = self._palette.get_room_color(segment_id)
