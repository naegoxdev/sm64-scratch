"""Recreate the imported coin strips from the ROM's alpha masks and macros."""
import struct
from sm64_scratch.rom_codec import mio0, decode_ia16
from sm64_scratch.decoders.decode_models import _fmt, _texture_tokens


def build_coin_models(rom, verified_by_model):
    segment = mio0(rom, 2102288)
    restored = []
    for frame, offset in enumerate((0x5780, 0x5F80, 0x6780, 0x6F80), 1):
        name = f'Yellow Coin Frame {frame}'
        texture = f'CoinYellowTexture{frame}'
        material = f'CoinYellowMaterial{frame}'
        image = dict(verified_by_model[name])[texture]
        source = decode_ia16(segment, offset, 32, 32)
        vertices, coords, faces = [], [], []
        # Each opaque row becomes a pair of front/back triangles, exactly as
        # in the project's authored conversion. No reference mesh is bundled.
        for y in range(32):
            opaque = [x for x in range(32) if source.getpixel((x, y))[3]]
            if not opaque:
                continue
            left, right = min(opaque), max(opaque) + 1
            x0, x1 = (left - 16) * .02, (right - 16) * .02
            z0, z1 = (31 - y) * .02, (32 - y) * .02
            u0, u1 = (left + .5) / 32, (right - .5) / 32
            v0, v1 = (y + .75) / 32, (y + .25) / 32
            first = len(vertices) + 1
            vertices.extend(((x0, 0, z0), (x1, 0, z1), (x1, 0, z0), (x0, 0, z1)))
            coords.extend(((u0, v0), (u1, v1), (u1, v0), (u0, v1)))
            for indices, normal in [((0, 1, 2), 1), ((0, 3, 1), 1),
                                    ((0, 2, 1), 2), ((0, 1, 3), 2)]:
                faces.append('f ' + ' '.join(f'{first + i}/{first + i}/{normal}' for i in indices))
        model = ' '.join(['o Coin',
            *('v ' + ' '.join(_fmt(c) for c in v) for v in vertices),
            *('vt ' + ' '.join(_fmt(c) for c in uv) for uv in coords),
            'vn 0 -1 0 vn 0 1 0', _texture_tokens(texture, image),
            f'newmtl {material} map_Kd {texture} usemtl {material}', *faces])
        restored.append((name, model, {'model_faces': len(faces), 'vertex_count': len(vertices),
                                      'geometry_source': 'ROM IA16 alpha row strips'}))
    return restored


def build_coin_placement_payload(rom):
    segment = mio0(rom, 3760960)
    # Area 1 macro-object table. The preset word includes the encoded yaw.
    start = 0x07077764 & 0xFFFFFF
    positions = []
    while start + 10 <= len(segment):
        preset, x, y, z, parameter = struct.unpack_from('>HhhhH', segment, start)
        start += 10
        if preset == 0x001E:
            break
        if preset & 0x1FF == 0x0020:
            positions.append((x / 100, y / 100, z / 100))
    if len(positions) != 4:
        raise ValueError('Expected four lobby coin macros in the US ROM')
    return {'Coin.' + axis: [round(p[i], 8) for p in positions]
            for i, axis in enumerate('XYZ')}
