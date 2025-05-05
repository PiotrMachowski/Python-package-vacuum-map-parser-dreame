"""Dreame map parser."""

import base64
import hashlib
import json
import logging
import re
import zlib
from enum import IntEnum, StrEnum
from typing import Any

from Crypto.Cipher import AES
from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.drawable import Drawable
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.config.size import Sizes
from vacuum_map_parser_base.config.text import Text
from vacuum_map_parser_base.map_data import Area, ImageData, MapData, Path, Point, Room, Wall
from vacuum_map_parser_base.map_data_parser import MapDataParser

from .header import MapDataHeader
from .image_parser import DreameImageParser
from .map_data_type import MapDataType

_LOGGER = logging.getLogger(__name__)


class PathOperator(StrEnum):
    """Dreame map path operator."""

    START = "S"
    RELATIVE_LINE = "L"


class FrameType(IntEnum):
    """Dreame map frame type."""

    I_FRAME = 73
    P_FRAME = 80


class DreameMapDataParser(MapDataParser):
    """Dreame map parser."""

    HEADER_SIZE = 27
    PATH_REGEX = r"(?P<operator>[SL])(?P<x>-?\d+),(?P<y>-?\d+)"
    IVs = {
        "dreame.vacuum.p2114a": "6PFiLPYMHLylp7RR",
        "dreame.vacuum.p2114o": "6PFiLPYMHLylp7RR",
        "dreame.vacuum.p2140o": "8qnS9dqgT3CppGe1",
        "dreame.vacuum.p2140p": "8qnS9dqgT3CppGe1",
        "dreame.vacuum.p2149o": "RNO4p35b2QKaovHC",
        "dreame.vacuum.r2209": "qFKhvoAqRFTPfKN6",
        "dreame.vacuum.r2211o": "dndRQ3z8ACjDdDMo",
        "dreame.vacuum.r2216o": "4sCv3Q2BtbWVBIB2",
        "dreame.vacuum.r2235": "NRwnBj5FsNPgBNbT",
        "dreame.vacuum.r2254": "wRy05fYLQJMRH6Mj",
    }

    def __init__( # pylint: disable=R0917
        self,
        palette: ColorsPalette,
        sizes: Sizes,
        drawables: list[Drawable],
        image_config: ImageConfig,
        texts: list[Text],
        model: str,
    ):
        super().__init__(palette, sizes, drawables, image_config, texts)
        self._image_parser = DreameImageParser(palette, image_config)
        self._iv: bytes | None = None
        iv = DreameMapDataParser.IVs.get(model)
        if iv is not None:
            self._iv = iv.encode("utf8")

    def unpack_map(
        self,
        raw_encoded: bytes,
        *args: Any,
        enckey: str | None = None,
        **kwargs: Any,
    ) -> bytes:
        raw_map_str = raw_encoded.decode().replace("_", "/").replace("-", "+")
        raw_map = base64.decodebytes(raw_map_str.encode("utf8"))
        if enckey is not None and self._iv is not None:
            _LOGGER.debug("Enc Key: %s", enckey)
            key = hashlib.sha256(enckey.encode()).hexdigest()[0:32].encode("utf8")
            raw_map_dec = AES.new(key, AES.MODE_CBC, iv=self._iv).decrypt(raw_map)
            unzipped = zlib.decompress(raw_map_dec)
        else:
            unzipped = zlib.decompress(raw_map)

        return unzipped

    def parse(self, raw: bytes, *args: Any, **kwargs: Any) -> MapData:
        parsed = self._parse_internal(raw, MapDataType.REGULAR)
        if parsed is None:
            raise NotImplementedError("Unsupported frame type")
        return parsed

    def _parse_internal(self, raw: bytes, map_data_type: MapDataType) -> MapData | None:
        map_data = MapData(0, 1000)

        header = DreameMapDataParser._parse_header(raw)

        if header is None or header.frame_type != FrameType.I_FRAME:
            _LOGGER.error("unsupported map frame type")
            return None

        if len(raw) >= DreameMapDataParser.HEADER_SIZE + header.image_width * header.image_height:
            image_raw = raw[
                DreameMapDataParser.HEADER_SIZE : DreameMapDataParser.HEADER_SIZE
                + header.image_width * header.image_height
            ]
            additional_data_raw = raw[DreameMapDataParser.HEADER_SIZE + header.image_width * header.image_height :]
            additional_data_json = json.loads(additional_data_raw.decode("utf8"))
            _LOGGER.debug("map additional_data: %s", str(additional_data_json))

            map_data.charger = header.charger_position
            map_data.vacuum_position = header.vacuum_position

            map_data.image, map_data.rooms = self._parse_image(image_raw, header, additional_data_json, map_data_type)
            if (
                additional_data_json.get("rism")
                and additional_data_json.get("ris")
                and additional_data_json["ris"] == 2
            ):
                decoded_rism_map_data = self.unpack_map(additional_data_json["rism"].encode("utf-8"))
                rism_map_data = self._parse_internal(decoded_rism_map_data, MapDataType.RISM)
                if rism_map_data is not None:
                    map_data.no_go_areas = rism_map_data.no_go_areas
                    map_data.no_mopping_areas = rism_map_data.no_mopping_areas
                    map_data.walls = rism_map_data.walls
                    map_data.rooms = rism_map_data.rooms
                    _LOGGER.debug("rooms: %s", str(map_data.rooms))

                    if rism_map_data.image is not None and not rism_map_data.image.is_empty:
                        map_data.image = rism_map_data.image

            if additional_data_json.get("tr"):
                map_data.path = DreameMapDataParser._parse_path(additional_data_json["tr"])

            if additional_data_json.get("vw"):
                if additional_data_json["vw"].get("rect"):
                    map_data.no_go_areas = DreameMapDataParser._parse_areas(additional_data_json["vw"]["rect"])
                if additional_data_json["vw"].get("mop"):
                    map_data.no_mopping_areas = DreameMapDataParser._parse_areas(additional_data_json["vw"]["mop"])
                if additional_data_json["vw"].get("line"):
                    map_data.walls = DreameMapDataParser._parse_virtual_walls(additional_data_json["vw"]["line"])

            if additional_data_json.get("sa") and isinstance(additional_data_json["sa"], list):
                map_data.additional_parameters["active_segment_ids"] = [sa[0] for sa in additional_data_json["sa"]]

            if map_data.image is not None and not map_data.image.is_empty:
                if map_data_type == MapDataType.REGULAR:
                    self._image_generator.draw_map(map_data)

        return map_data

    @staticmethod
    def _parse_header(raw: bytes) -> MapDataHeader | None:
        if not raw or len(raw) < DreameMapDataParser.HEADER_SIZE:
            _LOGGER.error("wrong header size for map")
            return None

        map_index = DreameMapDataParser._read_int_16_le(raw)
        frame_type = DreameMapDataParser._read_int_8(raw, 4)
        vacuum_position = Point(
            DreameMapDataParser._read_int_16_le(raw, 5),
            DreameMapDataParser._read_int_16_le(raw, 7),
            DreameMapDataParser._read_int_16_le(raw, 9),
        )
        charger_position = Point(
            DreameMapDataParser._read_int_16_le(raw, 11),
            DreameMapDataParser._read_int_16_le(raw, 13),
            DreameMapDataParser._read_int_16_le(raw, 15),
        )
        image_pixel_size = DreameMapDataParser._read_int_16_le(raw, 17)
        image_width = DreameMapDataParser._read_int_16_le(raw, 19)
        image_height = DreameMapDataParser._read_int_16_le(raw, 21)
        image_left = round(DreameMapDataParser._read_int_16_le(raw, 23) / image_pixel_size)
        image_top = round(DreameMapDataParser._read_int_16_le(raw, 25) / image_pixel_size)

        header = MapDataHeader(
            map_index,
            frame_type,
            vacuum_position,
            charger_position,
            image_pixel_size,
            image_width,
            image_height,
            image_left,
            image_top,
        )

        _LOGGER.debug("decoded map header: %s", str(header))

        return header

    def _parse_image(
        self, image_raw: bytes, header: MapDataHeader, additional_data_json: dict[str, Any], map_data_type: MapDataType
    ) -> tuple[ImageData, dict[int, Room]]:
        _LOGGER.debug("parse image for map %s", map_data_type)
        image, image_rooms = self._image_parser.parse(image_raw, header, map_data_type)
        if image is None:
            image = self._image_generator.create_empty_map_image()

        room_names = {}
        if additional_data_json.get("seg_inf"):
            room_names = {
                int(k): base64.b64decode(v.get("name")).decode("utf-8")
                for (k, v) in additional_data_json["seg_inf"].items()
                if v.get("name")
            }

        rooms = {
            k: Room(
                (v.x0 + header.image_left) * header.image_pixel_size,
                (v.y0 + header.image_top) * header.image_pixel_size,
                (v.x1 + header.image_left) * header.image_pixel_size,
                (v.y1 + header.image_top) * header.image_pixel_size,
                k,
                room_names[k] if room_names.get(k) else str(k),
            )
            for (k, v) in image_rooms.items()
        }

        return (
            ImageData(
                header.image_width * header.image_height,
                header.image_top,
                header.image_left,
                header.image_height,
                header.image_width,
                self._image_config,
                image,
                lambda p: DreameMapDataParser._map_to_image(p, header.image_pixel_size),
            ),
            rooms,
        )

    @staticmethod
    def _map_to_image(p: Point, image_pixel_size: int) -> Point:
        return Point(p.x / image_pixel_size, p.y / image_pixel_size)

    @staticmethod
    def _parse_path(path_string: str) -> Path:
        r = re.compile(DreameMapDataParser.PATH_REGEX)
        matches = [m.groupdict() for m in r.finditer(path_string)]

        current_path: list[Point] = []
        path_points = []
        current_position = Point(0, 0)
        for match in matches:
            if match["operator"] == PathOperator.START:
                current_path = []
                path_points.append(current_path)
                current_position = Point(int(match["x"]), int(match["y"]))
            elif match["operator"] == PathOperator.RELATIVE_LINE:
                current_position = Point(current_position.x + int(match["x"]), current_position.y + int(match["y"]))
            else:
                _LOGGER.error("invalid path operator %s", match["operator"])
            current_path.append(current_position)

        return Path(None, None, None, path_points)

    @staticmethod
    def _parse_areas(areas: list[tuple[int, int, int, int]]) -> list[Area]:
        parsed_areas = []
        for area in areas:
            x_coords = sorted([area[0], area[2]])
            y_coords = sorted([area[1], area[3]])
            parsed_areas.append(
                Area(
                    x_coords[0],
                    y_coords[0],
                    x_coords[1],
                    y_coords[0],
                    x_coords[1],
                    y_coords[1],
                    x_coords[0],
                    y_coords[1],
                )
            )
        return parsed_areas

    @staticmethod
    def _parse_virtual_walls(virtual_walls: list[tuple[int, int, int, int]]) -> list[Wall]:
        return [
            Wall(virtual_wall[0], virtual_wall[1], virtual_wall[2], virtual_wall[3]) for virtual_wall in virtual_walls
        ]

    @staticmethod
    def _read_int_8(data: bytes, offset: int = 0) -> int:
        return int.from_bytes(data[offset : offset + 1], byteorder="big", signed=True)

    @staticmethod
    def _read_int_8_le(data: bytes, offset: int = 0) -> int:
        return int.from_bytes(data[offset : offset + 1], byteorder="little", signed=True)

    @staticmethod
    def _read_int_16(data: bytes, offset: int = 0) -> int:
        return int.from_bytes(data[offset : offset + 2], byteorder="big", signed=True)

    @staticmethod
    def _read_int_16_le(data: bytes, offset: int = 0) -> int:
        return int.from_bytes(data[offset : offset + 2], byteorder="little", signed=True)
