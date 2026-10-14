"""Decode BOB objective placements and red coin billboards from the local ROM."""
import struct
from PIL import Image
from sm64_scratch.rom_codec import mio0, decode_ia16, decode_rgba16, decode_ia8
from sm64_scratch.decoders.decode_models import _texture_tokens


def build_bob_objective_payload(rom):
    segment = mio0(rom, 4178608)
    offset = 0x1104C
    coins, cannons = [], [(-56.94, 1.28, 56, 135)]
    while offset + 10 <= len(segment):
        word, x, y, z, parameter = struct.unpack_from('>HhhhH', segment, offset)
        offset += 10
        if word == 30:
            break
        preset = (word & 511) - 31
        if preset == 4:
            coins.append((x / 100, y / 100, z / 100))
        elif preset == 22:
            cannons.append((x / 100, y / 100, z / 100, (parameter & 255) * 360 / 256))
    if len(coins) != 8 or len(cannons) != 6:
        raise ValueError('Unexpected US BOB red coin/cannon macro table')
    result = {'BOB.RedCoin.' + axis: [p[i] for p in coins] for i, axis in enumerate('XYZ')}
    # Native coin shadows are projected onto the collision floor beneath the
    # parent object. Precompute the static BOB floor height for each red coin.
    from sm64_scratch.decoders.decode_collisions import parse_collision, _triangle_values
    from sm64_scratch.decoders.decode_bob_expansion import _floor
    vertices, triangles, _ = parse_collision(segment)
    floors = [_triangle_values(vertices, t) for t in triangles]
    result['BOB.RedCoin.ShadowY'] = [round(_floor(floors, x, y, z) + .01, 8)
                                     for x, y, z in coins]
    result.update({'BOB.Cannon.' + axis: [p[i] for p in cannons]
                   for i, axis in enumerate(('X', 'Y', 'Z', 'Yaw'))})
    return result


def build_red_coin_models(rom):
    """Two triangles per coin, native IA16 rotation frames, alpha key, no cull."""
    segment = mio0(rom, 2102288)
    result = []
    for frame, offset in enumerate((0x5780, 0x5F80, 0x6780, 0x6F80), 1):
        source = decode_ia16(segment, offset, 32, 32)
        image = Image.new('RGB', source.size)
        image.putdata([(r, 0, 0) if a else (254, 1, 254) for r, g, b, a in source.getdata()])
        texture = f'CoinRedTexture{frame}'
        text = ('o Coin v -.35 0 0 v .35 0 0 v .35 0 .7 v -.35 0 .7 '
                'vt 0 1 vt 1 1 vt 1 0 vt 0 0 vn 0 -1 0 ' + _texture_tokens(texture, image) +
                f' newmtl CoinRedMaterial{frame} nocull map_Kd {texture} '
                f'usemtl CoinRedMaterial{frame} f 1/1/1 3/3/1 2/2/1 f 1/1/1 4/4/1 3/3/1')
        result.append((f'Red Coin Frame {frame}', text,
                       {'model_faces': 2, 'vertex_count': 4,
                        'geometry_source': 'Billboard; native IA16 coin frame with red tint and keyed alpha'}))
    # GEO_SHADOW(SHADOW_CIRCLE_4_VERTS, 0xB4, 80): use the native segment-2
    # quarter-circle shadow texture mirrored into a full 32x32 footprint.
    common = mio0(rom, 1083968)
    quarter = decode_ia8(common, 0x120B8, 16, 16)
    shadow = Image.new('RGB', (32, 32))
    pixels = []
    for y in range(32):
        for x in range(32):
            alpha = quarter.getpixel((15-x if x < 16 else x-16,
                                      15-y if y < 16 else y-16))[3]
            pixels.append((0, 0, alpha) if alpha else (254, 1, 254))
    shadow.putdata(pixels)
    shadow_text = (_texture_tokens('RedCoinShadowTexture', shadow) +
        ' newmtl RedCoinShadowMaterial nocull map_Kd RedCoinShadowTexture '
        'v -.4 -.4 0 v .4 -.4 0 v .4 .4 0 v -.4 .4 0 '
        'vt 0 0 vt 1 0 vt 1 1 vt 0 1 vn 0 0 1 usemtl RedCoinShadowMaterial '
        'f 1/1/1 3/3/1 2/2/1 f 1/1/1 4/4/1 3/3/1')
    result.append(('BOB Red Coin Shadow', shadow_text,
                   {'model_faces': 2, 'vertex_count': 4, 'native_alpha': 180,
                    'native_shadow_diameter_cm': 80,
                    'geometry_source': 'Native red-coin circular shadow footprint',
                    'texture_source': 'US segment-2 IA8 quarter-circle mirrored into coverage map'}))
    return result


def build_red_coin_number_models(rom):
    """Native segment-2 digits on the 64 cm number billboard, not HUD text."""
    segment = mio0(rom, 1083968)
    result = []
    # red_coin.inc.c displays 1..7; the eighth pickup reveals the reward star.
    for number in range(1, 8):
        source = decode_rgba16(segment, number * 0x200, 16, 16, 'round')
        image = Image.new('RGB', source.size)
        image.putdata([(r, g, b) if a else (254, 1, 254)
                       for r, g, b, a in source.getdata()])
        texture = f'RedCoinNumberTexture{number}'
        material = f'RedCoinNumberMaterial{number}'
        # Camera-facing number quads are viewed from their reverse side in
        # this engine. Reverse U once, keeping the original ROM glyph upright.
        text = ('o Number v -.32 0 -.32 v .32 0 -.32 v .32 0 .32 v -.32 0 .32 '
                'vt 1 1 vt 0 1 vt 0 0 vt 1 0 vn 0 -1 0 ' + _texture_tokens(texture, image) +
                f' newmtl {material} nocull map_Kd {texture} usemtl {material} '
                'f 1/1/1 3/3/1 2/2/1 f 1/1/1 4/4/1 3/3/1')
        result.append((f'Red Coin Number {number}', text,
                       {'model_faces': 2, 'vertex_count': 4,
                        'geometry_source': 'Native segment-2 number billboard and RGBA16 HUD digit',
                        'texture_offset': number * 0x200}))
    return result
