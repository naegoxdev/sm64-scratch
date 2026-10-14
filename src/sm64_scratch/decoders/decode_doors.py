"""Decode the reference door meshes and hinge curves from the local US ROM."""
import hashlib
import json
import math
import struct
from pathlib import Path
from PIL import Image
from sm64_scratch.rom_codec import mio0
from sm64_scratch.decoders.decode_models import read_intro_logo, build_scene_model_text

from sm64_scratch.paths import METADATA_DIR


def build_door_models(rom, verified_by_model):
    recipes = json.loads((METADATA_DIR / 'doors.json').read_text())['models']
    restored = []
    for recipe in recipes:
        segment = mio0(rom, recipe['segment_rom_offset'])
        images = dict(verified_by_model.get(recipe['model'], []))
        white, yellow = [p & 0xFFFFFF for p in recipe['palette_lights']]
        # Four generic colour swatches: ambient white, diffuse yellow,
        # ambient yellow, diffuse white. Their colours come from ROM Lights1.
        colors = [tuple(segment[white:white + 3]), tuple(segment[yellow + 8:yellow + 11]),
                  tuple(segment[yellow:yellow + 3]), tuple(segment[white + 8:white + 11])]
        palette = Image.new('RGB', (16, 4))
        palette.putdata([colors[x // 4] for y in range(4) for x in range(16)])
        images[recipe['palette_texture']] = palette
        for name, expected in recipe['expected_texture_hashes'].items():
            if name not in images or hashlib.sha256(images[name].tobytes()).hexdigest() != expected:
                raise ValueError('Door texture differs from the reference: ' + name)
        rows, specs = [], {}
        skipped_decal_backings = 0
        for part in recipe['parts']:
            # The calibration recipe assigned the sign triangles twice: once
            # to the RGBA texture and again to an opaque palette swatch. The
            # second pass filled every transparent texel with ambient grey.
            opaque_decal = part['uv_method'] == 'constant' and any(
                    other['uv_method'] != 'constant'
                    and other['root_pointer'] == part['root_pointer']
                    and other['address_ranges'] == part['address_ranges']
                    and other['image_size'] != [32, 64]
                    for other in recipe['parts'])
            pointer = int(part['root_pointer'], 16)
            triangles, _ = read_intro_logo(segment, pointer, None, recipe['segment_id'])
            expected_pointer = int(part['pointer'], 16) if part['pointer'] else None
            for triangle in triangles:
                if triangle['texture_pointer'] != expected_pointer or not all(
                        any(lo <= v['source_address'] <= hi for lo, hi in part['address_ranges'])
                        for v in triangle['vertices']):
                    continue
                if opaque_decal:
                    skipped_decal_backings += 1
                    continue
                vertices = []
                for vertex in triangle['vertices']:
                    position = tuple(vertex['position'][a] * recipe['scale']
                                     + recipe['translation'][a] for a in range(3))
                    if part['uv_method'] == 'constant':
                        uv = part['uv_constant']
                    else:
                        uv = []
                        for st, size in zip(vertex['st'], part['image_size']):
                            value = st / (32 * size)
                            tile = math.floor(value)
                            uv.append(tile + (value - tile) * (1 - 1 / size) + .5 / size)
                    vertices.append({**vertex, 'position': position, 'uv': uv})
                rows.append((pointer, {**triangle, 'vertices': vertices, 'material': part['material']}))
            if not opaque_decal:
                specs[part['material']] = {**part, 'selection_key': part['material']}
        expected_faces = recipe['expected_face_count'] - skipped_decal_backings
        if len(rows) != expected_faces:
            raise ValueError('Door face selection is incomplete: ' + recipe['model'])
        model, report = build_scene_model_text(segment, {
            **recipe, 'coordinate_divisor': 1, 'display_list_pointers': [],
            'textures': list(specs.values()), 'minimum_triangles': expected_faces,
        }, list(images.items()), rows)
        report.update(verified_texture_count=len(images), reference_face_count=len(rows),
                      uv_max_reference_error=max(p['uv_max_error'] for p in recipe['parts']),
                      removed_opaque_decal_triangles=skipped_decal_backings)
        restored.append((recipe['model'], model, report))
    restored.append(build_eight_star_door(rom, verified_by_model['Lobby 8 Star Door']))
    return restored


def build_eight_star_door(rom, verified_textures):
    """Convert the native castle-door box into the authored eight-star door.

    The reference uses different dimensions and UV transforms on the six
    surfaces. Geometry and triangle connectivity still come from the ROM.
    """
    recipe = json.loads((METADATA_DIR / 'eight_star_door.json').read_text())
    segment = mio0(rom, recipe['segment_rom_offset'])
    root = int(recipe['root_pointer'], 16)
    triangles, _ = read_intro_logo(segment, root, None, recipe['segment_id'])
    if len(triangles) != recipe['reference_face_count']:
        raise ValueError('Unexpected native eight-star door box')
    points = [v['position'] for t in triangles for v in t['vertices']]
    low = [min(p[a] for p in points) for a in range(3)]
    high = [max(p[a] for p in points) for a in range(3)]
    rows = []
    for triangle in triangles:
        plane = next((p for p in recipe['planes'] if all(
            v['position'][p['axis']] == (high if p['side'] else low)[p['axis']]
            for v in triangle['vertices'])), None)
        if plane is None:
            raise ValueError('Door triangle does not belong to a calibrated surface')
        axes = [a for a in range(3) if a != plane['axis']]
        vertices = []
        for vertex in triangle['vertices']:
            fraction = [(vertex['position'][a] - low[a]) / (high[a] - low[a])
                        for a in range(3)]
            position = tuple((fraction[a] - (0 if a == 2 else .5))
                             * recipe['authored_dimensions'][a] + recipe['authored_origin'][a]
                             for a in range(3))
            u, v = (fraction[a] for a in axes)
            feature = (1, u, v, u * v)
            uv = tuple(round(sum(feature[k] * plane['uv_polynomial'][k][a]
                                 for k in range(4)), 6) for a in range(2))
            vertices.append({**vertex, 'position': position, 'uv': uv})
        rows.append((root, {**triangle, 'vertices': vertices, 'material': plane['material']}))
    images = dict(verified_textures)
    for name, expected in recipe['expected_texture_hashes'].items():
        if name not in images or hashlib.sha256(images[name].tobytes()).hexdigest() != expected:
            raise ValueError('Eight-star door texture differs: ' + name)
    specs = [{'material': f'Lobby8StarDoorMat{i}', 'texture': f'Lobby8StarDoorTex{i}',
              'selection_key': f'Lobby8StarDoorMat{i}'} for i in range(1, 4)]
    model, report = build_scene_model_text(segment, {
        'model': recipe['model'], 'object': 'Lobby_8_Star_Door',
        'coordinate_divisor': 1, 'display_list_pointers': [], 'textures': specs,
        'minimum_triangles': recipe['reference_face_count'],
    }, list(images.items()), rows)
    report['geometry_source'] = recipe['root_pointer']
    report['conversion'] = 'Authored box dimensions and bilinear surface UVs'
    return recipe['model'], model, report


def build_door_animation_payload(rom):
    segment = mio0(rom, 2102288)
    payload = {}
    for direction, pointer in [('Pull', 0x03015440), ('Push', 0x03015690)]:
        fields = struct.unpack_from('>6hIII', segment, pointer & 0xFFFFFF)
        values, indices = fields[6] & 0xFFFFFF, fields[7] & 0xFFFFFF
        count, start = struct.unpack_from('>HH', segment, indices + 7 * 4)
        if count != 80 or fields[4] != 80:
            raise ValueError('Unexpected ROM door animation layout')
        curve = [round(value * 360 / 65536, 6) for value in
                 struct.unpack_from('>80h', segment, values + start * 2)]
        payload['Door.' + direction + 'OpenCurve'] = curve[:37]
        payload['Door.' + direction + 'CloseCurve'] = curve[40:] + [0]
    return payload
