"""Reconstruct the Scratch garden conversion, including ROM moving water.

The world transform and decal offsets are engine calibration. Geometry and
pixels are decoded from the user's ROM; no private reference is opened here.
"""
import hashlib
import json
import struct
from pathlib import Path
from PIL import Image
from sm64_scratch.decoders.decode_models import build_scene_model_text, read_intro_logo
from sm64_scratch.rom_codec import decode_ia16, decode_rgba16, mio0, ROM_SHA1

from sm64_scratch.paths import METADATA_DIR
DEFAULT_RECIPE = METADATA_DIR / 'grounds.json'


def build_castle_grounds_model(rom, recipe=None, verified_textures=()):
    recipe = recipe or json.loads(DEFAULT_RECIPE.read_text())
    digest = hashlib.sha1(rom).hexdigest()
    if recipe['rom_sha1'] != digest or digest != ROM_SHA1:
        raise ValueError('Unsupported garden ROM')
    segments = {int(k): mio0(rom, v['rom_offset']) for k, v in recipe['segments'].items()}
    seg = segments[7]
    dx, dy, dz = recipe['world_translation']
    images = dict(verified_textures)
    bindings = {(r['root_pointer'], r['pointer']): r for r in recipe['material_bindings']}
    specs, rows, uncalibrated, commands = {}, [], set(), 0

    def ensure_material(material, pointer=None, size=(32, 32)):
        if material in specs:
            return
        name = material.replace('Mat', 'Tex')
        if name not in images:
            if pointer is None:
                image = Image.new('RGB', (8, 8))
            else:
                data = segments[pointer >> 24]
                fmt = recipe.get('texture_formats', {}).get(f'0x{pointer:08X}', 'rgba16')
                image = (decode_ia16(data, pointer & 0xFFFFFF, *size) if fmt == 'ia16'
                         else decode_rgba16(data, pointer & 0xFFFFFF, *size, 'floor'))
                # Preserve cutout texels using the renderer's existing colour key.
                rgba = image.convert('RGBA')
                keyed = Image.new('RGB', rgba.size)
                keyed.putdata([(r, g, b) if a > (46 if fmt == 'ia16' else 0)
                               else (254, 1, 254) for r, g, b, a in rgba.getdata()])
                image = keyed
            images[name] = image
            uncalibrated.add(name)
        specs[material] = {'material': material, 'texture': name,
                           'pointer': f'0x{pointer:08X}' if pointer else None,
                           'selection_key': material,
                           'nocull': material in ({'CastleLitMat' + str(i) for i in range(17, 23)}
                                                 | {'CastleLitMat63', 'CastleLitMat74'})}

    for root in recipe['display_list_pointers']:
        if root in recipe['excluded_display_lists']:
            continue
        triangles, count = read_intro_logo(seg, int(root, 16), None)
        commands += count
        for triangle in triangles:
            pointer = triangle['texture_pointer']
            if (root == '0x0700BA20' and pointer == 0x0900A800
                    and min(v['position'][2] for v in triangle['vertices']) > 4000):
                # Roof flags are separate animated objects in this Scratch scene.
                continue
            binding = bindings.get((root, f'0x{pointer:08X}' if pointer else None))
            if binding is None:
                raise ValueError('Unmapped garden material')
            colors = {v['color'][:3] for v in triangle['vertices']}
            color_key = ','.join(map(str, next(iter(colors)))) if len(colors) == 1 else ''
            material = binding['color_materials'].get(color_key, binding['default_material'])
            for choice in binding.get('address_materials', ()):
                if all(choice['min_source_address'] <= v['source_address'] <= choice['max_source_address']
                       for v in triangle['vertices']):
                    material = choice['material']
            ensure_material(material, pointer, triangle['texture_size'])
            offset = next((r for r in recipe['surface_offsets']
                           if r['root'] == root and r['pointer'] == f'0x{pointer:08X}'), {}) if pointer else {}
            vertices = []
            for vertex in triangle['vertices']:
                x, depth, height = vertex['position']
                vertices.append({**vertex, 'position':
                                 (x + dx * 100,
                                  depth - dz * 100 + offset.get('depth_offset', 0) * 100,
                                  height + dy * 100 + offset.get('vertical_offset', 0) * 100)})
            rows.append((int(root, 16), {**triangle, 'vertices': vertices, 'material': material}))

    water = recipe['movtex']
    material = water['material']
    ensure_material(material)
    # Movtex triples and their triangle indices are separate from static Gfx.
    start = int(water['waterfall_vertex_pointer'], 16) & 0xFFFFFF
    vertices = []
    for i in range(water['waterfall_vertices']):
        x, y, z, u, v = struct.unpack_from('>hhhhh', seg, start + i * 10)
        vertices.append({'position': (x + dx * 100, -z - dz * 100, y + dy * 100),
                         'uv': (u + 1 / 64, v + 1 / 64), 'st': (0, 0), 'color': (255,) * 4})
    cursor = int(water['waterfall_display_list_pointer'], 16) & 0xFFFFFF
    while True:
        w0, w1 = struct.unpack_from('>II', seg, cursor); cursor += 8
        if w0 >> 24 == 0xB8:
            break
        if w0 >> 24 != 0xBF:
            raise ValueError('Unsupported waterfall triangle command')
        ids = [(w1 >> bit & 255) // 10 for bit in (16, 8, 0)]
        if max(ids) >= len(vertices):
            raise ValueError('Waterfall vertex index is invalid')
        rows.append((0, {'vertices': [vertices[i] for i in ids], 'material': material,
                         'texture_pointer': None}))

    from sm64_scratch.decoders.decode_collisions import parse_collision
    boxes = parse_collision(seg, 0x0700F3A0)[2]
    for pointer, box in zip(water['quad_pointers'], boxes):
        off = int(pointer, 16) & 0xFFFFFF
        words = struct.unpack_from('>16h', seg, off)
        if words[0] != 1 or words[13] != 150:
            raise ValueError('Invalid garden water quad')
        scale = words[3]
        coords = [(words[4 + i * 2], words[5 + i * 2]) for i in range(4)]
        uv = [(1 / 64, 1 / 64), (1 / 64, scale - 47 / 1024),
              (scale - 47 / 1024, scale - 47 / 1024), (scale - 47 / 1024, 1 / 64)]
        quad = [{'position': (x + dx * 100, -z - dz * 100, (box['Level'] + dy) * 100),
                 'uv': t, 'st': (0, 0), 'color': (255,) * 4} for (x, z), t in zip(coords, uv)]
        for indices in ((0, 1, 2), (0, 2, 3)):
            rows.append((0, {'vertices': [quad[i] for i in indices], 'material': material,
                             'texture_pointer': None}))
    filtered = {**recipe, 'textures': list(specs.values()), 'vertex_color_atlas': None}
    model, report = build_scene_model_text(seg, filtered, list(images.items()), rows)
    report.update(world_translation=recipe['world_translation'], rom_sha1=digest,
                  display_list_commands=commands, water_quad_triangles=4, waterfall_triangles=17,
                  uncalibrated_textures=sorted(uncalibrated),
                  known_limitations=['Material selection on varying-normal faces and UV clamping need visual comparison.',
                                     'Water uses the reference static surface; no new animated water shader.'])
    return recipe['model'], model, report
