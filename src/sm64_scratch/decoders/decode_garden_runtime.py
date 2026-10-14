"""Recreate authored garden camera curves and ROM tree placement data."""
import hashlib
import json
import struct
from pathlib import Path
from PIL import Image
from sm64_scratch.rom_codec import mio0, decode_rgba16
from sm64_scratch.decoders.decode_models import _texture_tokens, read_intro_logo, build_scene_model_text
from sm64_scratch.decoders.decode_pipe_camera import build_pipe_camera_payload

from sm64_scratch.paths import METADATA_DIR


def build_garden_runtime(rom):
    recipe = json.loads((METADATA_DIR / 'grounds.json').read_text())
    segment = mio0(rom, recipe['segments']['7']['rom_offset'])
    tree = recipe['tree_placements']
    offset = int(tree['pointer'], 16) & 0xFFFFFF
    dx, dy, dz = recipe['world_translation']
    payload = {'Tree.' + axis: [] for axis in 'XYZ'}
    for i in range(tree['count']):
        preset, x, y, z = struct.unpack_from('>Hhhh', segment, offset + i * 8)
        if preset != tree['preset']:
            raise ValueError('Unexpected special object in tree placement table')
        for axis, value, delta in zip('XYZ', (x, y, z), (dx, dy, dz)):
            payload['Tree.' + axis].append(round(value / 100 + delta, 8))
    camera, camera_report = build_pipe_camera_payload(rom)
    payload.update(camera)
    # Runtime memory allocation includes the renderer's enabled flag.
    memory = [0] * 500
    memory[213] = 1
    payload['(Ram) Engine Data'] = memory
    return payload, {'tree_count': tree['count'], **camera_report,
                     'runtime_memory_words': len(memory)}


def build_tree_model(rom):
    """Use the reference billboard dimensions with the genuine ROM tree image.

    Texture resampling is still reported as uncalibrated, not an exact asset.
    """
    segment = mio0(rom, 2102288)
    atlas = Image.new('RGBA', (64, 64))
    atlas.paste(decode_rgba16(segment, 0x2DE28, 32, 64, 'floor'), (0, 0))
    atlas.paste(decode_rgba16(segment, 0x2EE28, 32, 64, 'floor'), (32, 0))
    # Keep native ROM pixels. The billboard's aspect ratio is the authored
    # Scratch model calibration; no alternative tree art is introduced.
    image = Image.new('RGB', atlas.size)
    image.putdata([(r, g, b) if a else (254, 1, 254) for r, g, b, a in atlas.getdata()])
    u0, u1, v0, v1 = .5 / 64, 1 - .5 / 64, .5 / 64, 1 - .5 / 64
    text = ('o CastleTree v -2.1 0 0 v 2.1 0 0 v 2.1 0 6 v -2.1 0 6 '
            f'vt {u0} {v1} vt {u1} {v1} vt {u1} {v0} vt {u0} {v0} '
            'vn 0 -1 0 vn 0 1 0 ' + _texture_tokens('CastleTree', image) +
            ' newmtl CastleTreeMat map_Kd CastleTree usemtl CastleTreeMat '
            'f 1/1/1 3/3/1 2/2/1 f 1/1/1 4/4/1 3/3/1 '
            'f 1/1/2 2/2/2 3/3/2 f 1/1/2 3/3/2 4/4/2')
    return 'Tree', text, {'model_faces': 4, 'vertex_count': 4,
                          'rgb_sha256': hashlib.sha256(image.tobytes()).hexdigest(),
                          'known_limitations': ['Native ROM tree texture; reference 45x64 resampling not yet matched.']}


def build_pipe_model(rom, verified_textures):
    """Apply the original imported pipe's affine transform to ROM vertices."""
    recipe = json.loads((METADATA_DIR / 'pipe.json').read_text())
    if hashlib.sha1(rom).hexdigest() != recipe['rom_sha1']:
        raise ValueError('Unsupported pipe ROM')
    segment = mio0(rom, recipe['segment_rom_offset'])
    closure = recipe['bottom_closure']
    light_offset = int(closure['light_pointer'], 16) & 0xFFFFFF
    light = segment[light_offset:light_offset + 24]
    if hashlib.sha256(light).hexdigest() != closure['light_sha256']:
        raise ValueError('Unexpected pipe closure lighting')
    # Native SHADE uses zero ambient and diffuse light for the bottom.
    # Represent that ROM-derived constant shade as one engine texel.
    if light[:3] != light[8:11]:
        raise ValueError('Pipe closure requires nonconstant lighting')
    closure_image = Image.new('RGB', (1, 1), tuple(light[:3]))
    rows = []
    closure_count = 0
    matrix = recipe['affine_transform']
    images = dict(verified_textures)
    image_for_pointer = {int(row['pointer'], 16): images[row['texture']]
                         for row in recipe['textures']}
    for root in recipe['display_list_pointers']:
        triangles, _ = read_intro_logo(segment, int(root, 16), None, 3)
        for triangle in triangles:
            is_closure = triangle['texture_pointer'] is None
            if is_closure:
                if triangle['light_pointer'] != int(closure['light_pointer'], 16):
                    raise ValueError('Unexpected untextured pipe surface')
                closure_count += 1
            vertices = []
            for index in recipe['vertex_order']:
                vertex = triangle['vertices'][index]
                source = (*vertex['position'], 1)
                position = tuple(sum(source[k] * matrix[k][a] for k in range(4)) for a in range(3))
                image = closure_image if is_closure else image_for_pointer[triangle['texture_pointer']]
                # Reference pipe images are vertically flipped during asset
                # conversion; flip V as well to preserve the imported mapping.
                uv = ((.5, .5) if is_closure else
                      (vertex['st'][0] / (32 * image.width),
                       1 - vertex['st'][1] / (32 * image.height)))
                vertices.append({**vertex, 'position': position, 'uv': uv})
            row = {**triangle, 'vertices': vertices}
            if is_closure:
                row['material'] = closure['material']
            rows.append((int(root, 16), row))
    if closure_count != closure['triangles']:
        raise ValueError('Incomplete native pipe bottom')
    recipe['textures'].append({'pointer': None, 'material': closure['material'],
                               'texture': closure['texture']})
    model, report = build_scene_model_text(segment, recipe,
        [*verified_textures, (closure['texture'], closure_image)], rows)
    report['native_bottom_closure_triangles'] = closure_count
    report['native_bottom_light_sha256'] = closure['light_sha256']
    report['calibration_max_coordinate_error'] = recipe['calibration_max_coordinate_error']
    return recipe['model'], model, report
