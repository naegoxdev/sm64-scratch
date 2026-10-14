"""Recover the authored BOB painting mesh from native ROM painting tables."""
import hashlib
import math
import struct
from sm64_scratch.decoders.decode_models import read_intro_logo, _texture_tokens, _fmt
from sm64_scratch.rom_codec import mio0


def _uv(st):
    # The original importer uses tile-local half-texel coordinates, including
    # the negative and over-one texture coordinates at the painting edges.
    result = []
    for value, size in zip(st, (64, 32)):
        value /= 32 * size
        tile = math.floor(value)
        result.append(round(tile + (value - tile) * (1 - 1 / size) + .5 / size, 6))
    return result


def build_painting_model(rom, verified_textures):
    lobby, shared = mio0(rom, 3760960), mio0(rom, 1083968)
    count = struct.unpack_from('>h', shared, 0x178C0)[0]
    if count != 157:
        raise ValueError('Unexpected native painting mesh layout')
    mesh = [struct.unpack_from('>hhh', shared, 0x178C2 + i * 6) for i in range(count)]
    images = dict(verified_textures)
    if set(images) != {'BobPaintingTex1', 'BobPaintingTex2'}:
        raise ValueError('Painting needs both verified ROM image halves')
    vertices, coords, faces, index = [], [], [], {}
    for triangle in read_intro_logo(lobby, 0x07023050, None, 7)[0]:
        material = 1 if triangle['texture_pointer'] == 0x0700B800 else 2
        ids = []
        for corner in (0, 2, 1):
            v = triangle['vertices'][corner]
            key = v['source_address']
            if key not in index:
                index[key] = len(vertices) + 1
                vertices.append(tuple(c / 100 for c in v['position']))
            coords.append(_uv(v['st']))
            ids.append((index[key], len(coords)))
        faces.append((material, ids))
    for pointer, material in ((0x21AE0, 3), (0x21FFC, 4)):
        mappings = struct.unpack_from('>h', lobby, pointer)[0]
        if mappings != 85:
            raise ValueError('Unexpected painting texture mapping count')
        mapping = [struct.unpack_from('>hhh', lobby, pointer + 2 + i * 6)
                   for i in range(mappings)]
        face_count = struct.unpack_from('>h', lobby, pointer + 2 + mappings * 6)[0]
        if face_count != 132:
            raise ValueError('Unexpected painting triangle mapping count')
        start, uv_start = len(vertices) + 1, len(coords) + 1
        for mesh_id, s, t in mapping:
            x, z, movable = mesh[mesh_id]
            # The Scratch reference offsets ripple-capable vertices by one
            # ROM unit. Keep the static border on the unshifted plane.
            vertices.append((x / 100, -movable / 100, z / 100))
            coords.append(_uv((s, t)))
        for i in range(face_count):
            ids = struct.unpack_from('>hhh', lobby, pointer + 4 + mappings * 6 + i * 6)
            faces.append((material, [(start + ids[c], uv_start + ids[c]) for c in (0, 2, 1)]))
    if (len(vertices), len(faces)) != (178, 268):
        raise ValueError('Painting mesh differs from the authored layout')
    tokens = [_texture_tokens(name, image) for name, image in images.items()]
    for material in range(1, 5):
        texture = 1 if material in (1, 3) else 2
        tokens.append(f'newmtl BobPaintingMat{material} map_Kd BobPaintingTex{texture}')
    tokens.extend('v ' + ' '.join(_fmt(c) for c in v) for v in vertices)
    tokens.extend('vt ' + ' '.join(_fmt(c) for c in uv) for uv in coords)
    active = None
    for material, ids in faces:
        if material != active:
            tokens.append(f'usemtl BobPaintingMat{material}')
            active = material
        tokens.append('f ' + ' '.join(f'{v}/{t}/1' for v, t in ids))
    return 'Bob Painting', ' '.join(tokens), {
        'vertex_count': len(vertices), 'model_faces': len(faces),
        'texture_rgb_sha256': {name: hashlib.sha256(im.tobytes()).hexdigest() for name, im in images.items()},
        'geometry_source': 'ROM normal display list, native shared mesh and texture maps; authored one-unit ripple offset'}
