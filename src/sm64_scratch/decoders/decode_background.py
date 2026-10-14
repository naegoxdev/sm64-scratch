#!/usr/bin/env python3
"""Rebuild the Super Mario 64 title-screen backdrop from a local ROM.

The title screen background is a small four-tile RGBA16 image in the ROM's
MIO0-compressed title-screen segment.  The Scratch stage uses a 480x360
backdrop, so the 80x80 ROM tile is repeated and enlarged with nearest-neighbour
sampling.  Only offsets and dimensions are kept in the package; the pixels are
decoded from the user's ROM while building the SB3.
"""
from __future__ import annotations

import hashlib

from PIL import Image

from sm64_scratch.rom_codec import ROM_SHA1, decode_rgba16, mio0


TITLE_BACKGROUND_SIZE = (480, 360)
TITLE_TILE_SIZE = (80, 20)


class TitleBackgroundDecodeError(ValueError):
    pass


def build_title_background(rom: bytes, descriptor: dict) -> tuple[Image.Image, dict]:
    """Decode the title backdrop and return an RGB image plus an audit report."""
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        raise TitleBackgroundDecodeError(
            "Intro background is verified only for the supported US ROM"
        )
    if descriptor.get("rom_sha1") != ROM_SHA1:
        raise TitleBackgroundDecodeError("Background map does not match the US ROM")
    segment_offset = int(descriptor["segment_rom_offset"])
    segment = mio0(rom, segment_offset)
    tile_width, tile_height = map(int, descriptor.get("tile_size", TITLE_TILE_SIZE))
    tile_offsets = [int(value) for value in descriptor["tile_offsets"]]
    if len(tile_offsets) != 4:
        raise TitleBackgroundDecodeError("Intro background must contain four tiles")
    if (tile_width, tile_height) != TITLE_TILE_SIZE:
        raise TitleBackgroundDecodeError("Unverified background tile dimensions")
    tile_bytes = tile_width * tile_height * 2
    if any(offset < 0 or offset + tile_bytes > len(segment) for offset in tile_offsets):
        raise TitleBackgroundDecodeError("Background tile outside ROM segment")

    strip = Image.new("RGBA", (tile_width, tile_height * len(tile_offsets)))
    for row, offset in enumerate(tile_offsets):
        tile = decode_rgba16(segment, offset, tile_width, tile_height, "floor")
        strip.paste(tile, (0, row * tile_height))

    # The N64 title-screen display list repeats the 80x80 strip over the screen.
    # Keep the source pixels crisp: this is pixel art, not a filtered photograph.
    tiled = Image.new("RGBA", (tile_width * 4, tile_height * len(tile_offsets) * 3))
    for y in range(3):
        for x in range(4):
            tiled.paste(strip, (x * tile_width, y * strip.height))
    image = tiled.resize(TITLE_BACKGROUND_SIZE, Image.Resampling.NEAREST).convert("RGB")
    report = {
        "rom_sha1": ROM_SHA1,
        "segment_rom_offset": segment_offset,
        "segment_decoded_size": len(segment),
        "tile_offsets": tile_offsets,
        "tile_size": [tile_width, tile_height],
        "source_size": list(tiled.size),
        "output_size": list(image.size),
        "rgb_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
    }
    return image, report


def build_title_background_runtime_payload(image: Image.Image) -> dict[str, list[int]]:
    """Encode the ROM backdrop for the renderer's 240×180 sky buffer.

    The Scratch renderer clears its software framebuffer from
    ``TitleIntro.Background240`` rather than sampling the Stage costume.  The
    list stores packed RGB integers in row-major order, so reconstruct the
    same buffer from the ROM image at build time.
    """
    # The framebuffer presents its scanlines from bottom to top.  Its X axis
    # keeps the image's reading order: reversing X here mirrors every logo.
    # Compensate only for Y in the runtime list; the Stage costume remains in
    # ordinary image coordinates.  This matches the reference renderer's
    # TitleIntro.Background240 convention.
    runtime_image = image.convert('RGB').transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    small = runtime_image.resize((240, 180), Image.Resampling.NEAREST)
    values = [(red << 16) | (green << 8) | blue
              for red, green, blue in small.getdata()]
    if len(values) != 240 * 180:
        raise TitleBackgroundDecodeError('Background buffer must contain 43200 pixels')
    return {'TitleIntro.Background240': values}
