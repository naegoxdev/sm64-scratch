"""Rebuild Mario's authored texture and shade atlas from local ROM pixels."""
import hashlib
import json
from pathlib import Path
from PIL import Image
from sm64_scratch.rom_codec import mio0, decode_rgba16

from sm64_scratch.paths import METADATA_DIR


def build_mario_atlas(rom):
    recipe = json.loads((METADATA_DIR / 'mario_atlas.json').read_text())
    segment = mio0(rom, 0x114750)
    base = Image.new('RGB', (256, 256), (255, 0, 255))
    base.paste((114, 28, 14), (0, 128, 64, 192))
    base.paste((115, 6, 0), (64, 128, 128, 192))
    base.paste((255, 255, 255), (0, 192, 64, 256))
    for row in recipe['textures']:
        width, height = row['source_size']
        src = decode_rgba16(segment, row['pointer'], width, height, 'floor')
        if row.get('remove_isolated_alpha'):
            # The authored logo conversion removes disconnected single pixels.
            alpha = src.getchannel('A')
            for y in range(height):
                for x in range(width):
                    neighbours = [(x-1, y), (x+1, y), (x, y-1), (x, y+1)]
                    if alpha.getpixel((x, y)) and not any(alpha.getpixel((nx, ny))
                            for nx, ny in neighbours if 0 <= nx < width and 0 <= ny < height):
                        r, g, b, a = src.getpixel((x, y))
                        src.putpixel((x, y), (r, g, b, 0))
        src = src.resize((64, 64), Image.Resampling.NEAREST)
        if row['transpose'] is not None:
            src = src.transpose(Image.Transpose(row['transpose']))
        pixels = []
        for pixel in src.getdata():
            if not pixel[3]:
                pixels.append(tuple(row['alpha_zero_color']))
            else:
                pixels.append(tuple(row['lut'][c][str(pixel[c])] for c in range(3)))
        tile = Image.new('RGB', (64, 64))
        tile.putdata(pixels)
        base.paste(tile, tuple(row['atlas_origin']))
    atlas = Image.new('RGB', (256, 1024))
    atlas.paste(base, (0, 0))
    for band, luts in enumerate(recipe['shade_luts'], 1):
        shaded = Image.merge('RGB', [channel.point([luts[c].get(str(v), v) for v in range(256)])
                                   for c, channel in enumerate(base.split())])
        atlas.paste(shaded, (0, band * 256))
    if hashlib.sha256(atlas.tobytes()).hexdigest() != recipe['expected_rgb_sha256']:
        raise ValueError('Mario texture/shade atlas differs from the private reference hash')
    return atlas
