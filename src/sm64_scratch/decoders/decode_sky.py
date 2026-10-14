#!/usr/bin/env python3
"""Rebuild the Castle Grounds water skybox solely from a user-owned ROM."""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from PIL import Image

from sm64_scratch.rom_codec import ROM_SHA1, mio0


from sm64_scratch.paths import METADATA_DIR
DEFAULT_MANIFEST = METADATA_DIR / "sky.json"


class SkyboxDecodeError(ValueError):
    pass


def decode_skybox_atlas(rom: bytes, manifest: dict) -> Image.Image:
    if manifest.get("format") != 13 or manifest.get("rom_sha1") != hashlib.sha1(rom).hexdigest():
        raise SkyboxDecodeError("Sky recipe does not match the supported US ROM")
    offset = int(manifest["rom_offset"])
    segment = mio0(rom, offset)
    tile_size = int(manifest["tile_size"])
    tile_bytes = tile_size * tile_size * 2
    columns = int(manifest["columns"])
    rows = int(manifest["rows"])
    pointer_columns = int(manifest["pointer_columns"])
    pointer_rows = int(manifest["pointer_rows"])
    pointer_bytes = pointer_columns * pointer_rows * 4
    table_start = len(segment) - pointer_bytes
    if table_start != int(manifest["tile_data_bytes"]):
        raise SkyboxDecodeError("Skybox MIO0 length does not match the recipe")
    if table_start != columns * rows * tile_bytes:
        raise SkyboxDecodeError("Tile pointer table does not follow image data")
    pointers = struct.unpack_from(">" + "I" * (pointer_columns * pointer_rows),
                                 segment, table_start)
    base_pointer = pointers[0]
    tile_indices = []
    for row in range(rows):
        for col in range(columns):
            difference = pointers[row * pointer_columns + col] - base_pointer
            if difference < 0 or difference % tile_bytes:
                raise SkyboxDecodeError("Misaligned tile pointer in sky map")
            index = difference // tile_bytes
            if index >= columns * rows:
                raise SkyboxDecodeError("Tile pointer is outside sky image data")
            tile_indices.append(index)

    size = tile_size * columns
    image = Image.new("RGB", (size, tile_size * rows))
    pixels = image.load()
    for row in range(rows):
        for col in range(columns):
            tile_index = tile_indices[row * columns + col]
            tile_offset = tile_index * tile_bytes
            for y in range(tile_size):
                for x in range(tile_size):
                    value = struct.unpack_from(">H", segment, tile_offset + 2 * (y * tile_size + x))[0]
                    r5, g5, b5 = (value >> 11) & 31, (value >> 6) & 31, (value >> 1) & 31
                    rgb = (r5 * 255 // 31, g5 * 255 // 31, b5 * 255 // 31)
                    pixels[col * tile_size + x, row * tile_size + y] = rgb
    return image


def build_sky_texture_payload(rom: bytes, manifest: dict | None = None):
    manifest = manifest or json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    image = decode_skybox_atlas(rom, manifest)
    output_width = int(manifest["output_width"])
    output_height = int(manifest["output_height"])
    repeats = int(manifest["horizontal_repeats"])
    if output_width % repeats:
        raise SkyboxDecodeError("Width is not divisible by horizontal repetitions")
    tile = image.resize((output_width // repeats, output_height), Image.Resampling.BILINEAR)
    # Scratch samples a row-major atlas using y * output_width + x.
    # Repeat each row horizontally; repeating the entire flattened image
    # would interleave two source rows and restart the sky halfway down.
    packed = [(r << 16) | (g << 8) | b for r, g, b in tile.getdata()]
    tile_width = tile.width
    pixels = []
    for y in range(output_height):
        row = packed[y * tile_width:(y + 1) * tile_width]
        pixels.extend(row * repeats)
    if len(pixels) != output_width * output_height:
        raise SkyboxDecodeError("Generated sky map has an incorrect length")
    if pixels[0] == 0 or len(set(pixels)) < 128:
        raise SkyboxDecodeError("Generated sky map appears empty or corrupt")
    report = {
        "skybox": manifest["name"],
        "rom_offset": offset_hex(manifest["rom_offset"]),
        "atlas_size": list(image.size),
        "output_size": [output_width, output_height],
        "horizontal_repeats": repeats,
        "pixel_layout": "row-major, horizontal repetitions within each row",
        "pixels": len(pixels),
        "distinct_rgb_colors": len(set(pixels)),
        "source_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
    }
    return {manifest["destination_list"]: pixels}, report


def offset_hex(value) -> str:
    return "0x%X" % int(value)
