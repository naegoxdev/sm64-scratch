#!/usr/bin/env python3
"""Rebuild the small in-game HUD sprites from the user's ROM.

The US segment 2 contains the original colored 16x16 HUD glyphs.  The
Scratch project stores the number costumes at 24x24 pixels (the same glyph
enlarged with nearest-neighbour sampling), and stores the icon strip in a
480x360 transparent costume.  This module keeps only the segment offset and
glyph offsets; all image bytes are decoded during the local build.
Segment 3 supplies the native power-meter frame and eight health wedges.
"""
from __future__ import annotations

import hashlib

from PIL import Image, ImageDraw

from sm64_scratch.rom_codec import ROM_SHA1, decode_rgba16, mio0


HUD_SEGMENT_ROM_OFFSET = 1083968
HUD_GLYPH_SIZE = (16, 16)
HUD_NUMBER_SIZE = (24, 24)

# US segment 2 has no JP-only J/Q glyphs, so the later special glyphs are
# packed at 0x4200..0x4800 rather than at their source labels 0x5600..0x5C00.
HUD_GLYPH_OFFSETS = {
    "multiply": 0x4200,
    "coin": 0x4400,
    "mario_head": 0x4600,
    "star": 0x4800,
}

# US ROM offsets from the segment-2 camera LUT. Arrows are 8x8 RGBA16;
# the camera/mode icons are 16x16. Source filenames retain JP offsets.
HUD_CAMERA_OFFSETS = {
    'camera': 0x7000, 'Lakitu': 0x7200, 'Fixed': 0x7400,
    'Mario': 0x4600, 'Up': 0x7600, 'Down': 0x7680,
}


class HudDecodeError(ValueError):
    pass


def _glyph(segment: bytes, offset: int) -> Image.Image:
    end = offset + HUD_GLYPH_SIZE[0] * HUD_GLYPH_SIZE[1] * 2
    if offset < 0 or end > len(segment):
        raise HudDecodeError("HUD glyph is outside the ROM segment")
    return decode_rgba16(segment, offset, *HUD_GLYPH_SIZE, "round").convert("RGBA")


def build_camera_hud_assets(rom: bytes):
    """Compose the native camera/status/arrow glyphs in screen coordinates."""
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        raise HudDecodeError('Camera HUD requires the supported US ROM')
    segment = mio0(rom, HUD_SEGMENT_ROM_OFFSET)
    icons = {name: decode_rgba16(segment, offset, 8 if name in ('Up','Down') else 16,
                                8 if name in ('Up','Down') else 16, 'round').convert('RGBA')
             for name, offset in HUD_CAMERA_OFFSETS.items()}
    costumes = []
    for mode in ('Lakitu', 'Mario', 'Fixed'):
        for arrow in ('', 'Up', 'Down'):
            image = Image.new('RGBA', (480,360))
            image.alpha_composite(icons['camera'].resize((24,24), Image.Resampling.NEAREST), (399,308))
            image.alpha_composite(icons[mode].resize((24,24), Image.Resampling.NEAREST), (423,308))
            if arrow:
                image.alpha_composite(icons[arrow].resize((12,12), Image.Resampling.NEAREST),
                                      (405,296 if arrow == 'Up' else 332))
            costumes.append((mode + (' '+arrow if arrow else ''), image))
    return {'SM64 HUD Camera': costumes}, dict(
        source='US ROM segment 2 RGBA16 camera LUT',
        glyph_offsets=HUD_CAMERA_OFFSETS, native_position=[266,205],
        screen_position=[399,308], scale=1.5, costumes=len(costumes),
        rgba_sha256={name: hashlib.sha256(image.tobytes()).hexdigest() for name,image in costumes})


def build_hud_assets(rom: bytes) -> tuple[dict[str, list[tuple[str, Image.Image]]], dict]:
    """Return HUD costume images grouped by target sprite and an audit report."""
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        raise HudDecodeError("HUD is supported only for the specified US ROM")
    segment = mio0(rom, HUD_SEGMENT_ROM_OFFSET)
    digits = {
        str(value): _glyph(segment, value * 0x200)
        for value in range(10)
    }
    icons = {
        name: _glyph(segment, offset)
        for name, offset in HUD_GLYPH_OFFSETS.items()
    }

    number_costumes = [(name, image.resize(HUD_NUMBER_SIZE, Image.Resampling.NEAREST))
                       for name, image in digits.items()]

    # hud.c and print.c: native 320x240 coordinates scaled by 1.5.
    # print_text uses y = 224 - HUD_TOP_Y (209 in US), and advances by 12.
    # Coins are course-only in the original game, so keep variants with the
    # entire coin pair (icon + multiplier) absent for castle/grounds.
    def hud_strip(include_coin: bool, include_star_multiplier: bool):
        image = Image.new("RGBA", (480, 360), (0, 0, 0, 0))
        glyph = lambda name: icons[name].resize(HUD_NUMBER_SIZE, Image.Resampling.NEAREST)
        image.alpha_composite(glyph("mario_head"), (33, 22))
        image.alpha_composite(glyph("multiply"), (57, 22))
        if include_coin:
            image.alpha_composite(glyph("coin"), (252, 22))
            image.alpha_composite(glyph("multiply"), (276, 22))
        image.alpha_composite(glyph("star"), (363, 22))
        if include_star_multiplier:
            image.alpha_composite(glyph("multiply"), (387, 22))
        return image

    strip = hud_strip(True, True)
    no_star_x = hud_strip(True, False)
    no_coin = hud_strip(False, True)
    no_coin_no_star_x = hud_strip(False, False)
    assets = {
        "SM64 HUD": [
            ("HUD icons", strip),
            ("HUD icons no star multiplier", no_star_x),
            ("HUD icons no coin", no_coin),
            ("HUD icons no coin no star multiplier", no_coin_no_star_x),
        ],
    }
    from sm64_scratch.patches.patch_compact_hud import COUNTERS, digit_target_name
    for counter, _source, _x, _maximum in COUNTERS:
        for slot in range(1,4):
            assets[digit_target_name(counter,slot)] = [(name,image.copy())
                                                      for name,image in number_costumes]
    camera_assets, camera_report = build_camera_hud_assets(rom)
    assets.update(camera_assets)
    # Native segment-3 power meter: two 32x64 halves and eight 32x32
    # wedge images. Preserve the complete 64x64 native mesh projection.
    meter_segment = mio0(rom, 2102288)
    meter = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    meter.alpha_composite(decode_rgba16(meter_segment, 0x233E0, 32, 64, "round"), (0, 0))
    meter.alpha_composite(decode_rgba16(meter_segment, 0x243E0, 32, 64, "round"), (32, 0))
    meter_costumes = []
    for wedges in range(9):
        image = meter.copy()
        if wedges:
            offset = 0x253E0 + (8 - wedges) * 0x800
            image.alpha_composite(decode_rgba16(meter_segment, offset, 32, 32, "round"), (16, 16))
        image = image.resize((96, 96), Image.Resampling.NEAREST)
        meter_costumes.append((str(wedges), image))
    assets["SM64 Power Meter"] = meter_costumes
    # The native horizontal impact particle is geometry, not a texture.
    # Rasterize its ROM display list into the existing eight billboard sprites.
    from sm64_scratch.decoders.decode_models import read_intro_logo
    triangles, _ = read_intro_logo(meter_segment, 0x0302C318, None, segment_id=3)
    if len(triangles) != 8:
        raise HudDecodeError("Unexpected native ground-pound particle mesh")
    points = [v['position'] for t in triangles for v in t['vertices']]
    xmin, xmax = min(p[0] for p in points), max(p[0] for p in points)
    ymin, ymax = min(p[2] for p in points), max(p[2] for p in points)
    scale = 28 / max(xmax - xmin, ymax - ymin)
    star = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(star)
    for triangle in triangles:
        vertices = triangle['vertices']
        draw.polygon([(16 + (v['position'][0] - (xmin + xmax) / 2) * scale,
                       16 - (v['position'][2] - (ymin + ymax) / 2) * scale)
                      for v in vertices], fill=vertices[0]['color'])
    for slot in range(1, 9):
        assets[f'Ground Pound Star {slot}'] = [('Ground Pound Star', star.copy())]
    report = {
        "rom_sha1": ROM_SHA1,
        "segment_rom_offset": HUD_SEGMENT_ROM_OFFSET,
        "segment_decoded_size": len(segment),
        "glyph_size": list(HUD_GLYPH_SIZE),
        "number_size": list(HUD_NUMBER_SIZE),
        "number_costumes": len(number_costumes),
        "hud_sprites_rebuilt": 12,
        "number_sprites": 9,
        "number_costume_slots": 90,
        "camera_hud": camera_report,
        "power_meter_costumes": 9,
        "ground_pound_particle_sprites": 8,
        "ground_pound_particle_source": "US segment 3 display list 0x0302C318, eight native yellow triangles",
        "ground_pound_particle_rgba_sha256": hashlib.sha256(star.tobytes()).hexdigest(),
        "power_meter_source": "US ROM segment 3, native frame and health wedge textures",
        "power_meter_reference_pixel_exact": False,
        "power_meter_note": "Full 64x64 native frame at 1.5x, no cropping or filtered stretching; zero-health costume contains no wedge.",
        "number_advance": 18,
        "lives_costumes": 10,
        "glyph_offsets": {name: offset for name, offset in HUD_GLYPH_OFFSETS.items()},
        "strip_size": list(strip.size),
        "strip_variants": 4,
        "strip_rgba_sha256": hashlib.sha256(strip.tobytes()).hexdigest(),
    }
    return assets, report
