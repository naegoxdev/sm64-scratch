#!/usr/bin/env python3
"""Rebuild title-intro scale tables directly from a user's US ROM.

Only segment offsets and table lengths are stored in the porter. The animation
samples are decoded from the local ROM at build time and inserted into the
existing Scratch lists.
"""
from __future__ import annotations

import math
import struct
import hashlib

from PIL import Image

from sm64_scratch.rom_codec import ROM_SHA1, mio0, decode_rgba16


# These are the raw RGBA16 tiles used by Goddard's title effects in the
# supported US image.  They are offsets into the user's ROM, never bytes that
# are shipped with the porter.
TITLE_EFFECT_ROM_OFFSETS = {
    "hand_open": (0x258D80, 32, 32),
    "hand_closed": (0x259588, 32, 32),
    # Each entry is one 32x32 RGBA16 frame.  The old metadata described the
    # number of frames as the tile width, which made the report misleading and
    # made callers that honour the dimensions decode truncated rows.
    "red_star": (0x259D88, 32, 32),
    "silver_star": (0x25DD88, 32, 32),
    # gd_texture_sparkle_0 (assets.json and renderer.c). Successive tiles
    # occupy 0x800 bytes; tile 5 is present in the ROM but unused by Goddard.
    "sparkle": (0x262088, 32, 32),
}

TITLE_PRESS_START_GLYPHS = {'P': 0x3000, 'R': 0x3200, 'E': 0x1C00,
                           'S': 0x3400, 'T': 0x3600, 'A': 0x1400}


def build_title_press_start_assets(rom: bytes):
    """Compose the two native HUD-font lines from US segment 2, locally."""
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        raise ValueError('PRESS START requires the supported US ROM')
    segment = mio0(rom, 0x108A40)
    canvas = Image.new('RGBA', (320, 240), (0, 0, 0, 0))
    hashes = {}
    for text, y in [('PRESS', 38), ('START', 20)]:
        for index, char in enumerate(text):
            offset = TITLE_PRESS_START_GLYPHS[char]
            tile = decode_rgba16(segment, offset, 16, 16, 'round')
            canvas.alpha_composite(tile, (60 - len(text) * 6 + index * 12, 224 - y))
            hashes[char] = hashlib.sha256(segment[offset:offset + 512]).hexdigest()
    image = canvas.resize((480, 360), Image.Resampling.NEAREST)
    return {'Title Intro PRESS START': [('PRESS START', image)]}, {
        'title_press_start_segment': 'MIO0 0x108A40',
        'title_press_start_glyph_offsets': {c: hex(v) for c, v in TITLE_PRESS_START_GLYPHS.items()},
        'title_press_start_glyph_sha256': hashes,
        'title_press_start_native_lines': [['PRESS', 60, 38], ['START', 60, 20]],
        'title_press_start_blink': 'global_timer modulo 32 < 20',
    }

# Segment 7 offsets, not addresses added to the compressed ROM address.
# US assets.json / intro_seg7_dl_copyright: original RGBA16 tiles and UVs.
TITLE_COPYRIGHT_SEGMENT_ROM_OFFSET = 0x26A3A0
TITLE_COPYRIGHT_TILES = {
    "copyright": (0xB4A0, 128, 16,
                  "2a5e3e79c10aac5517329abc6d588162a772d6f9ff84c726c85bcbad992aadbc"),
    "tm": (0xC4A0, 16, 16,
           "5b228438ebd719951ff30bc1b4413795ce40b4d8e72b74504629339639212c9c"),
}


def _title_decal_quad(tile: Image.Image, uv_width: int) -> Image.Image:
    """Map a native quad to 1.5x pixels using clamped three-point filtering.

    TM's right UV is 544/32 = 17, although its tile/quad are 16 wide.
    Preserve that mapping rather than drawing replacement text or mirroring.
    As with the wipe, this does not emulate RDP edge coverage or VI filtering.
    """
    width, height = tile.size
    out = Image.new("RGBA", (width * 3 // 2, height * 3 // 2))
    def texel(x, y):
        return tile.getpixel((max(0, min(width - 1, x)), max(0, min(height - 1, y))))
    for y in range(out.height):
        t, ft = divmod(math.floor((y + 0.5) / 1.5 * 32), 32)
        for x in range(out.width):
            s, fs = divmod(math.floor((x + 0.5) / 1.5 * uv_width / width * 32), 32)
            a, b, c, d = texel(s, t), texel(s + 1, t), texel(s, t + 1), texel(s + 1, t + 1)
            if fs == ft == 16:
                value = tuple((a[i] + b[i] + c[i] + d[i] + 2) // 4 for i in range(4))
            elif fs + ft <= 32:
                value = tuple((a[i] * 32 + (b[i] - a[i]) * fs + (c[i] - a[i]) * ft + 16) // 32 for i in range(4))
            else:
                value = tuple((d[i] * 32 + (c[i] - d[i]) * (32 - fs) + (b[i] - d[i]) * (32 - ft) + 16) // 32 for i in range(4))
            out.putpixel((x, y), value)
    return out


def build_title_copyright_assets(rom: bytes):
    """Rebuild copyright/TM from the user's US ROM into the existing overlay."""
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        raise ValueError("Intro copyright overlay is verified only for the US ROM")
    segment = mio0(rom, TITLE_COPYRIGHT_SEGMENT_ROM_OFFSET)
    image = Image.new("RGBA", (480, 360))
    sources = {}
    # C uses a bottom-left 320x240 orthographic screen. Scratch uses a
    # top-left bitmap: copyright (96,42)..(224,58), TM (268,180)..(284,196).
    for name, left, top, uv_width in (("copyright", 144, 273, 128), ("tm", 402, 66, 17)):
        offset, width, height, expected = TITLE_COPYRIGHT_TILES[name]
        tile_bytes = segment[offset:offset + width * height * 2]
        digest = hashlib.sha256(tile_bytes).hexdigest()
        if digest != expected:
            raise ValueError("Could not verify ROM texture: " + name)
        tile = decode_rgba16(segment, offset, width, height, "floor")
        image.paste(_title_decal_quad(tile, uv_width), (left, top))
        sources[name] = dict(decoded_offset=hex(offset), size=[width, height],
                             sha256=digest, output_top_left=[left, top], uv_width=uv_width)
    return {"Title Intro Copyright TM": [("copyright_tm", image)]}, {
        "title_intro_copyright_tm_from_rom": True,
        "title_intro_copyright_tm_segment_rom_offset": hex(TITLE_COPYRIGHT_SEGMENT_ROM_OFFSET),
        "title_intro_copyright_tm_codec": "MIO0/RGBA16",
        "title_intro_copyright_tm_sources": sources,
        "title_intro_copyright_tm_output_size": [480, 360],
        "title_intro_copyright_tm_filter": "G_TF_BILERP (three-point/clamp)",
    }


def build_title_logo_fade_assets():
    """C's plain fullscreen black quad, not an extracted texture.

    level_intro_splash_screen requests FADE_INTO_COLOR for16 frames.
    screen_transition.c computes round(t*255/(16-1)): 0,17,...,255.
    Generate these untextured primitive frames locally with the same alpha.
    """
    alphas = [int(frame * 255 / 15 + 0.5) for frame in range(16)]
    return {"Title Intro Logo Fade": [
        (f"fade_{frame:02d}", Image.new("RGBA", (480, 360), (0, 0, 0, alpha)))
        for frame, alpha in enumerate(alphas)
    ]}, {
        "title_intro_logo_fade_restored": True,
        "title_intro_logo_fade_source": "untextured_black_quad/screen_transition.c",
        "title_intro_logo_fade_frames": 16,
        "title_intro_logo_fade_alphas": alphas,
        "title_intro_logo_fade_logo_frames": [76, 91],
        "title_intro_logo_fade_covers_copyright_tm": True,
    }

# The original game calls this IA8 tile ``texture_transition_star_half`` in
# ``screen_transition.c``.  It is a 32x64 half tile in segment 2; the game
# mirrors it while changing the transition radius.  The Scratch intro stores
# the same wipe as twenty 480x360 costumes, so the porter rasterises the
# five-point mask locally instead of shipping any extracted PNG.
TITLE_TRANSITION_STAR_SEGMENT_ROM_OFFSET = 0x108A40
TITLE_TRANSITION_STAR_DECODED_OFFSET = 0x122B8
TITLE_TRANSITION_STAR_SHA256 = "e754d82c0cc9e353964b71735ad00bedf30a9ecbd62057cf109d6f708ab49dae"
TITLE_TRANSITION_STAR_BYTES = 32 * 64
TITLE_TRANSITION_STAR_FRAMES = 20

# Legacy reference-view visibility used only by the calibrated head light
# pass. These are NOT timing gates in Goddard draw_light. Actual billboards
# use the moving camera, native clipping and the shared depth buffer.
TITLE_STAR_DRAW_WINDOWS = {
    "Silver": ((205, 254), (261, 262), (269, 270), (317, 402),
                (466, 467), (473, 474), (476, 480), (517, 518), (522, 538)),
    "Red": ((339, 345), (376, 413), (456, 487)),
}


def _mirror_transition_texel(index: int, size: int) -> int:
    """RDP wrap/mirror addressing, including negative S coordinates."""
    index %= size * 2
    return index if index < size else size * 2 - index - 1


def _transition_alpha_sample(alpha: bytes, s_fixed: int, t_fixed: int) -> int:
    """Sample the 32x64 half tile with G_TF_BILERP's three-point filter.

    Texture coordinates have five fractional bits. Keep the ROM alpha levels;
    neither threshold the silhouette nor add a blur to the resulting mask.
    The RDP's exact half-texel special case averages all four neighbours.
    """
    s, fs = divmod(s_fixed, 32)
    t, ft = divmod(t_fixed, 32)
    x0, x1 = (_mirror_transition_texel(s + i, 32) for i in (0, 1))
    y0, y1 = (_mirror_transition_texel(t + i, 64) * 32 for i in (0, 1))
    a, b, c, d = alpha[y0 + x0], alpha[y0 + x1], alpha[y1 + x0], alpha[y1 + x1]
    if fs == ft == 16:
        return (a + b + c + d + 2) // 4
    if fs + ft <= 32:
        return (a * 32 + (b - a) * fs + (c - a) * ft + 16) // 32
    return (d * 32 + (c - d) * (32 - fs) + (b - d) * (32 - ft) + 16) // 32


def _render_transition_star(alpha: bytes, radius: int) -> Image.Image:
    """Map C's centred quad and UVs (-31..31, 0..63) to Scratch pixels.

    The native screen is 320x240, scaled uniformly by 1.5 to 480x360. The
    outside of the quad stays opaque black, as in the filled-region display
    list. This raster reproduces the texture/mapping/filter, not every detail
    of RDP edge coverage or the console's final video-interface filtering.
    """
    width, height = 480, 360
    pixels = bytearray([255]) * (width * height)
    if radius:
        # Pixel centres, sampled directly from the half tile. Pillow resizing
        # a drawn polygon would erase the original asset's rounded gradients.
        s_coords = []
        for x in range(width):
            dx = (x + 0.5) / 1.5 - 160
            s_coords.append(math.floor(dx * 31 * 32 / radius)
                            if -radius <= dx < radius else None)
        for y in range(height):
            dy = (y + 0.5) / 1.5 - 120
            if not -radius <= dy < radius:
                continue
            t_fixed = math.floor((dy / radius + 1) * 31.5 * 32)
            for x, s_fixed in enumerate(s_coords):
                if s_fixed is not None:
                    pixels[y * width + x] = _transition_alpha_sample(alpha, s_fixed, t_fixed)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    image.putalpha(Image.frombytes("L", (width, height), bytes(pixels)))
    return image


def build_title_star_reveal_assets(rom: bytes):
    """Decode the actual IA8 star and reproduce the twenty-tick C wipe.

    Segment 2 is MIO0-compressed: its decoded offset cannot be added to the
    compressed ROM address. The original texture already contains soft alpha
    edges. Its shape and opacity are read locally, never generated as a
    substitute polygon or shipped as extracted images.
    """
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        raise ValueError("Star transition is verified only for the US ROM")
    segment = mio0(rom, TITLE_TRANSITION_STAR_SEGMENT_ROM_OFFSET)
    end = TITLE_TRANSITION_STAR_DECODED_OFFSET + TITLE_TRANSITION_STAR_BYTES
    if end > len(segment):
        raise ValueError("Star transition texture out of ROM bounds")
    tile = segment[TITLE_TRANSITION_STAR_DECODED_OFFSET:end]
    if hashlib.sha256(tile).hexdigest() != TITLE_TRANSITION_STAR_SHA256:
        raise ValueError("Could not verify texture_transition_star_half in the ROM")
    alpha = bytes((value & 15) * 17 for value in tile)

    assets = {"Title Intro Star Reveal": []}
    width, height = 480, 360
    # area.c: 0 -> GFX_DIMENSIONS_FULL_RADIUS (320 for the US 4:3 screen).
    # screen_transition.c rounds the radius to s16 after dividing by time-1.
    radii = [int(frame * 320 / (TITLE_TRANSITION_STAR_FRAMES - 1) + 0.5)
             for frame in range(TITLE_TRANSITION_STAR_FRAMES)]
    for frame in range(TITLE_TRANSITION_STAR_FRAMES):
        image = _render_transition_star(alpha, radii[frame])
        assets["Title Intro Star Reveal"].append((f"star_{frame:02d}", image))
    return assets, {
        "title_intro_star_reveal_from_rom": True,
        "title_intro_star_reveal_decomp_source": "sm64/src/game/screen_transition.c",
        "title_intro_star_reveal_decomp_texture": "texture_transition_star_half",
        "title_intro_star_reveal_frames": TITLE_TRANSITION_STAR_FRAMES,
        "title_intro_star_reveal_segment_rom_offset": hex(TITLE_TRANSITION_STAR_SEGMENT_ROM_OFFSET),
        "title_intro_star_reveal_decoded_offset": hex(TITLE_TRANSITION_STAR_DECODED_OFFSET),
        "title_intro_star_reveal_codec": "MIO0/IA8",
        "title_intro_star_reveal_source_sha256": hashlib.sha256(tile).hexdigest(),
        "title_intro_star_reveal_source_bytes": TITLE_TRANSITION_STAR_BYTES,
        "title_intro_star_reveal_output_size": [width, height],
        "title_intro_star_reveal_native_radii": radii,
        "title_intro_star_reveal_filter": "G_TF_BILERP (three-point)",
        "title_intro_star_reveal_asset_alpha_preserved": True,
        "title_intro_star_reveal_extra_blur": False,
    }


def _effect_tile(rom: bytes, offset: int, width: int = 32, height: int = 32):
    end = offset + width * height * 2
    if offset < 0 or end > len(rom):
        raise ValueError("Effect texture out of ROM bounds")
    # The ROM's original extractor expands 5-bit channels by truncation.
    return decode_rgba16(rom, offset, width, height, "floor")


def build_title_effect_assets(rom: bytes):
    """Decode the hand, stars and sparkle frames used by the title intro.

    Scratch keeps two copies of each star costume (the renderer chooses the
    copy for the current subframe) and twelve sparkle names while the ROM has
    six sparkle tiles.  The name mapping below mirrors the original display
    list sequence and is derived from the ROM texture tables.
    """
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        raise ValueError("Intro effects are verified only for the US ROM")
    assets = {
        "Title Intro Goddard Hand": [
            ("hand_open", _effect_tile(rom, *TITLE_EFFECT_ROM_OFFSETS["hand_open"])),
            ("hand_closed", _effect_tile(rom, *TITLE_EFFECT_ROM_OFFSETS["hand_closed"])),
        ],
        "Title Intro Silver Star": [],
        "Title Intro Red Star": [],
        "Title Intro Silver Sparkle2": [],
        "Title Intro Red Sparkle2": [],
        "Title Intro Grab Sparkles": [],
    }
    silver0 = TITLE_EFFECT_ROM_OFFSETS["silver_star"][0]
    red0 = TITLE_EFFECT_ROM_OFFSETS["red_star"][0]
    for index in range(8):
        silver = _effect_tile(rom, silver0 + index * 0x800,
                              TITLE_EFFECT_ROM_OFFSETS["silver_star"][1],
                              TITLE_EFFECT_ROM_OFFSETS["silver_star"][2]).resize(
            (128, 128), Image.Resampling.NEAREST)
        red = _effect_tile(rom, red0 + index * 0x800,
                           TITLE_EFFECT_ROM_OFFSETS["red_star"][1],
                           TITLE_EFFECT_ROM_OFFSETS["red_star"][2]).resize(
            (128, 128), Image.Resampling.NEAREST)
        for subframe in (0, 1):
            assets["Title Intro Silver Star"].append((
                f"silver_star_{index}_{subframe}", silver.copy()))
            assets["Title Intro Red Star"].append((
                f"red_star_{index}_{subframe}", red.copy()))
    # renderer.c indexes this array by timeout - 1, not elapsed age.
    sparkle_sequence = (4, 4, 3, 3, 2, 2, 1, 1, 0, 0, 4, 4)
    sparkle0 = TITLE_EFFECT_ROM_OFFSETS["sparkle"][0]
    for index, source_index in enumerate(sparkle_sequence, start=1):
        image = _effect_tile(rom, sparkle0 + source_index * 0x800,
                             TITLE_EFFECT_ROM_OFFSETS["sparkle"][1],
                             TITLE_EFFECT_ROM_OFFSETS["sparkle"][2])
        assets["Title Intro Silver Sparkle2"].append((
            f"silver_timeout_{index:02d}", image.copy()))
        assets["Title Intro Red Sparkle2"].append((
            f"red_timeout_{index:02d}", image.copy()))
        # draw_particle writes sLightColours, but this indexed display list
        # overrides primitive colour with (255,255,255,255) and uses
        # G_CC_MODULATERGBA_PRIM. Its timeout selects the tile; it does NOT
        # darken the texture. Baking timeout/10 produced opaque black stars.
        assets["Title Intro Grab Sparkles"].append((
            f"grab_timeout_{index:02d}", image.copy()))
    copyright_assets, copyright_report = build_title_copyright_assets(rom)
    assets.update(copyright_assets)
    fade_assets, fade_report = build_title_logo_fade_assets()
    assets.update(fade_assets)
    # The assetless base also removes this untextured presentation quad.
    # Recreate it locally so the existing exit fade has a visible surface.
    assets['Title Intro Gameplay Fade'] = [
        ('White fade', Image.new('RGBA', (480, 360), (255, 255, 255, 255)))]
    press_assets, press_report = build_title_press_start_assets(rom)
    assets.update(press_assets)
    # Transparent positioning costumes have no ROM pixels. Scratch fences
    # sprites according to the full costume rectangle, even if transparent.
    from sm64_scratch.patches.patch_effects import ANCHORS, BILLBOARDS
    for target in BILLBOARDS:
        assets[target] += [(name, Image.new('RGBA', (720, 540), (0, 0, 0, 0)))
                          for name, _x, _y in ANCHORS]
    return assets, {
        **copyright_report,
        **fade_report,
        **press_report,
        "title_intro_effect_costumes_total": sum(len(v) for v in assets.values()),
        "title_intro_effect_costumes_from_rom": sum(len(v) for v in assets.values()) - 37,
        "title_intro_exit_fade_generated": True,
        "title_intro_effect_sources": {
            key: {"rom_offset": hex(value[0]), "source_size": list(value[1:]),
                  "tile_count": 8 if key.endswith('_star') else 6 if key == 'sparkle' else 1}
            for key, value in TITLE_EFFECT_ROM_OFFSETS.items()
        },
        "title_intro_star_source_size": [32, 32],
        "title_intro_star_output_size": [128, 128],
        "title_intro_sparkle_timeout_tiles": list(sparkle_sequence),
    }


def build_star_animation_payload(rom: bytes, frame_count: int = 820):
    """Project the ROM's two title-star animation tracks into Scratch space.

    Goddard uses a 320×240 view and a camera at (0, 200, 2000). Decode all
    820 source frames. Billboard visibility is evaluated at render time;
    legacy light-pass visibility remains separate from drawing eligibility.
    """
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        raise ValueError("Star animations are verified only for the US ROM")
    root_rows = struct.unpack_from(">" + "h" * (820 * 6), rom, 0x29D56C)
    sources = {
        "Silver": struct.unpack_from(">" + "h" * (820 * 6), rom, 0x2A03C8),
        "Red": struct.unpack_from(">" + "h" * (820 * 6), rom, 0x2A3224),
    }

    def project(row):
        # gd_mat4f_lookat(0,200,2000 -> 0,200,0) transforms the Goddard
        # animation position to (x, y-200, z-2000). Render reflects its own
        # view X afterward, so Goddard +X remains screen-right on the stage.
        x, y, z = (float(row[3]), float(row[4]), float(row[5]))
        depth = 2000.0 - z
        if depth <= 30.0:
            return 160.0, 120.0, depth, False
        screen_x = 160.0 + x * 256.0 / depth
        screen_y = 120.0 - (y - 200.0) * 256.0 / depth
        return screen_x, screen_y, depth, True

    output = {}
    # The reference intro animates its lights for 660 frames, then holds the
    # last direction during the remaining facial poses.  The lists still
    # need 820 entries because the controller indexes them by AnimFrame.
    # Continuing the ROM star tracks past that visual interval sends the
    # white light behind the face and darkens the generated idle head.
    light_frame_count = len(root_rows) // 6
    report = {
        "title_intro_star_animation_frames_from_rom": frame_count,
        "title_intro_light_animation_frames_from_rom": light_frame_count,
        "title_intro_light_active_frames": 660,
        "title_intro_light_hold_from_frame": 660,
        "title_intro_star_render": "native_128_world_quad_shared_depth_buffer",
        "title_intro_star_draw_gates": "camera_near_far_and_viewport_no_baked_windows",
        "title_intro_light_prelude_frames": 69,
        "title_intro_light_track_delay": 3,
        "title_intro_light_direction_quantization": "round_127_then_normalize",
    }
    for colour, rows in sources.items():
        xs, ys, sizes, visible = [], [], [], []
        light_x, light_y, light_z = [], [], []
        for index in range(frame_count):
            row = rows[index * 6:index * 6 + 6]
            x, y, depth, projectable = project(row)
            # 128 world-unit quad, focal length 256 in native pixels;
            # sprites previously multiplied this percentage by 1.5.
            size = 25600.0 / depth if depth > 0 else 0
            show = int(depth > 256 and any(start <= index <= end
                                           for start, end in TITLE_STAR_DRAW_WINDOWS[colour]))
            xs.append(round(x, 4)); ys.append(round(y, 4)); sizes.append(round(size, 4)); visible.append(show)
        # Match the existing Scratch light export: the 69-frame entrance is
        # sampled directly; the following section uses a three-frame delay.
        # Root and light must use the SAME sample, rather than subtracting a
        # delayed star position from the current root position.  Quantize the
        # unit direction to the exporter's signed 127-scale components and
        # normalize it again.  These conversion rules regenerate the private
        # reference's light lists exactly from the ROM, without copying them.
        for index in range(light_frame_count):
            active_index = min(index, 659)
            source_index = active_index if active_index < 69 else active_index - 3
            root = root_rows[source_index * 6:source_index * 6 + 6]
            row = rows[source_index * 6:source_index * 6 + 6]
            dx = float(row[3] - root[3])
            dy = float(row[4] - root[4])
            dz = float(row[5] - root[5])
            norm = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            dx, dy, dz = (round(value / norm * 127.0) for value in (dx, dy, dz))
            norm = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            light_x.append(round(dx / norm, 7))
            light_y.append(round(dy / norm, 7))
            light_z.append(round(dz / norm, 7))
        prefix = f"TitleIntro.{colour}Star"
        # Keep positions in world space so camera motion also projects the
        # stars and their trails. These samples remain build-time ROM data.
        for axis, component in zip('XYZ', (3, 4, 5)):
            output[f"{prefix}World{axis}"] = [float(rows[i * 6 + component]) for i in range(frame_count)]
        output[f"{prefix}Draw"] = [1] * frame_count
        # Separate alpha sentinel preserves opaque black texels; public BASE
        # carries only empty lists. All pixel values originate in the ROM.
        offset = TITLE_EFFECT_ROM_OFFSETS[colour.lower() + '_star'][0]
        pixels = []
        for tile in range(8):
            image = _effect_tile(rom, offset + tile * 0x800, 32, 32)
            pixels.extend((r << 16 | g << 8 | b) if a else -1
                          for r, g, b, a in image.getdata())
        output[f"{prefix}Pixels"] = pixels
        output[f"{prefix}X"] = xs
        output[f"{prefix}Y"] = ys
        output[f"{prefix}Size"] = sizes
        output[f"{prefix}Visible"] = visible
        light_prefix = f"TitleIntro.{colour}Light"
        output[f"{light_prefix}X"] = light_x
        output[f"{light_prefix}Y"] = light_y
        output[f"{light_prefix}Z"] = light_z
        report[f"title_intro_{colour.lower()}_visible_frames"] = sum(visible)
    return output, report


def build_logo_scale_payload(rom: bytes, recipe: dict) -> tuple[dict[str, list], dict]:
    if recipe.get('rom_sha1') != ROM_SHA1:
        raise ValueError('Intro tables do not match the supported US ROM.')
    segment = mio0(rom, int(recipe['logo_segment_rom_offset']))
    zoom_in_frames = int(recipe['logo_zoom_in_frames'])
    hold_frames = int(recipe['logo_hold_frames'])
    zoom_out_frames = int(recipe['logo_zoom_out_frames'])
    zoom_in_offset = int(recipe['logo_scale_zoom_in_segment_offset'])
    zoom_out_offset = int(recipe['logo_scale_zoom_out_segment_offset'])
    expected_zoom_out_offset = zoom_in_offset + zoom_in_frames * 3 * 4
    if zoom_out_offset != expected_zoom_out_offset:
        raise ValueError('Intro scale tables are not contiguous as expected.')
    end = zoom_out_offset + zoom_out_frames * 3 * 4
    if zoom_in_frames <= 0 or hold_frames < 0 or zoom_out_frames <= 0 or end > len(segment):
        raise ValueError('Intro scale tables outside ROM segment.')

    zoom_in = struct.unpack_from('>' + 'f' * (zoom_in_frames * 3), segment, zoom_in_offset)
    zoom_out = struct.unpack_from('>' + 'f' * (zoom_out_frames * 3), segment, zoom_out_offset)
    values = [*zoom_in, *([1.0] * (hold_frames * 3)), *zoom_out]
    if not all(math.isfinite(value) and 0.0 <= value <= 2.0 for value in values):
        raise ValueError('Intro scale table contains invalid values.')
    if len(recipe.get('logo_destination_lists', [])) != 3:
        raise ValueError('Expected three destination lists for logo scale.')
    channels = [values[index::3] for index in range(3)]
    expected_frames = zoom_in_frames + hold_frames + zoom_out_frames
    if any(len(channel) != expected_frames for channel in channels):
        raise ValueError('The three logo scale lists have different lengths.')
    payload = dict(zip(recipe['logo_destination_lists'], channels))
    report = {
        'title_intro_logo_scale_frames_from_rom': expected_frames,
        'title_intro_logo_zoom_in_frames': zoom_in_frames,
        'title_intro_logo_hold_frames': hold_frames,
        'title_intro_logo_zoom_out_frames': zoom_out_frames,
    }
    return payload, report


def build_head_animation_payload(rom: bytes, recipe: dict) -> tuple[dict[str, list], dict]:
    """Decode Goddard's original 820-frame head animation from the ROM.

    ``animdata_mario_intro_1`` is an array of six big-endian signed shorts in
    the US image.  The first three fields are rotations in tenths of a degree;
    the last three are the X/Y/Z root translation.  Keeping the decoder here
    means the public builder needs only the user's ROM and does not ship the
    decompilation source or a copied Scratch animation table.
    """
    if recipe.get('rom_sha1') != ROM_SHA1:
        raise ValueError('Head animation does not match the supported US ROM.')
    offset = int(recipe['head_animation_rom_offset'])
    frames = int(recipe['head_animation_frames'])
    divisor = float(recipe.get('head_animation_angle_divisor', 10.0))
    if offset < 0 or frames <= 0 or divisor == 0 or offset + frames * 12 > len(rom):
        raise ValueError('Head animation table out of ROM bounds.')
    rows = struct.unpack_from('>' + 'h' * (frames * 6), rom, offset)
    samples = [rows[index:index + 6] for index in range(0, len(rows), 6)]
    rotations = [[row[index] / divisor for row in samples] for index in range(3)]
    positions = [[row[index] for row in samples] for index in range(3, 6)]
    payload = {
        'TitleIntro.RootRX': rotations[0],
        'TitleIntro.RootRY': rotations[1],
        'TitleIntro.RootRZ': rotations[2],
        'TitleIntro.RootX': positions[0],
        'TitleIntro.RootY': positions[1],
        'TitleIntro.RootZ': positions[2],
    }
    if any(len(values) != frames for values in payload.values()):
        raise ValueError('All six head animation tracks must have the same length.')
    report = {
        'title_intro_head_animation_frames_from_rom': frames,
        'title_intro_head_animation_rom_offset': hex(offset),
        'title_intro_head_animation_tracks': 6,
    }
    return payload, report
