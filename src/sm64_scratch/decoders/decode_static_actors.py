"""Rebuild static actor meshes from ROM display lists and conversion recipes."""
import hashlib
import json
import struct
from pathlib import Path
import numpy as np
from PIL import Image
from sm64_scratch.rom_codec import mio0, decode_rgba16, decode_ia16
from sm64_scratch.decoders.decode_models import read_intro_logo, _texture_tokens, _fmt

from sm64_scratch.paths import METADATA_DIR


def build_static_actor_models(rom, verified_by_model):
    recipes = json.loads((METADATA_DIR / 'static_actors.json').read_text())['models']
    segments, restored = {}, []
    for recipe in recipes:
        bank = recipe['segment_rom_offset']
        if bank not in segments:
            segments[bank] = mio0(rom, bank)
        segment = segments[bank]
        images = dict(verified_by_model.get(recipe['model'], []))
        if recipe.get('palette'):
            p = recipe['palette']
            # A single authored material tint, not a distributed texture image.
            images[p['texture']] = Image.new('RGB', tuple(p['size']), tuple(p['color']))
        for name, spec in recipe.get('rom_palettes', {}).items():
            colors = [tuple(segment[offset:offset + 3]) for offset in spec['rgb_offsets']]
            image = Image.new('RGB', tuple(spec['size']))
            image.putdata([colors[x // spec['stripe_width']]
                           for y in range(image.height) for x in range(image.width)])
            images[name] = image
        for name, spec in recipe.get('rom_images', {}).items():
            image = Image.new('RGB', tuple(spec['size']))
            for tile in spec['tiles']:
                part = (decode_ia16(segment, tile['offset'], *tile['size'])
                        if tile.get('pixel_format') == 'ia16' else
                        decode_rgba16(segment, tile['offset'], *tile['size'], tile['decode_mode']))
                rgb = part.convert('RGB')
                if tile.get('alpha_encoding') == 'blue_channel':
                    rgb.putdata([(255, 255, alpha) for alpha in part.getchannel('A').getdata()])
                if tile.get('alpha_key'):
                    rgb.putdata([color if alpha else (254, 1, 254)
                                 for color, alpha in zip(rgb.getdata(), part.getchannel('A').getdata())])
                if tile.get('transpose') is not None:
                    rgb = rgb.transpose(Image.Transpose(tile['transpose']))
                image.paste(rgb, tuple(tile['origin']))
            images[name] = image
        for name, spec in recipe.get('procedural_metal', {}).items():
            # Reproduce the Scratch-authored metal pattern, including its four
            # rivets. This is a procedural material; it is not a ROM texture.
            image = Image.new('RGB', tuple(spec['size']))
            edge, base, stripe, rivet = map(tuple, spec['colors'])
            w, h = image.size
            image.putdata([edge if x in (0, w - 1) or y in (0, h - 1) else
                           rivet if (x, y) in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3)) else
                           stripe if (x + y) % 3 == 0 else base
                           for y in range(h) for x in range(w)])
            images[name] = image
        for name, expected in recipe['expected_texture_hashes'].items():
            if name not in images or hashlib.sha256(images[name].tobytes()).hexdigest() != expected:
                raise ValueError('Static actor texture differs from the reference: ' + name)
        bindings = {(b['root_pointer'], b['texture_pointer'], b['light_pointer']): b
                    for b in recipe['bindings']}
        matrix = np.array(recipe['affine_transform'])
        vertices, coords, faces, normals, index = [], [], [], [], {}
        for root in recipe['roots']:
            triangles = read_intro_logo(segment, root, None, recipe['segment_id'])[0]
            if recipe.get('full_billboard'):
                # The imported Scratch actor joins the native half-billboards
                # and adds a reverse side. Derive its bounds from ROM vertices.
                points = np.array([v['position'] for t in triangles for v in t['vertices']])
                lo, hi = points.min(axis=0), points.max(axis=0)
                corners = [(lo[0], 0, lo[2]), (hi[0], 0, lo[2]),
                           (hi[0], 0, hi[2]), (lo[0], 0, hi[2])]
                st = [(0, 1024), (1024, 1024), (1024, 0), (0, 0)]
                triangles = [{'texture_pointer': None, 'light_pointer': None,
                              'vertices': [{'position': corners[i], 'st': st[i]} for i in ids]}
                             for ids in ((0, 2, 1), (0, 3, 2), (0, 1, 2), (0, 2, 3))]
            for triangle in triangles:
                binding = bindings[(root, triangle['texture_pointer'], triangle['light_pointer'])]
                refs = []
                for corner in recipe['corner_order']:
                    vertex = triangle['vertices'][corner]
                    point = tuple(np.array((*vertex['position'], 1)) @ matrix)
                    if point not in index:
                        index[point] = len(vertices) + 1
                        vertices.append(point)
                    st = np.array(vertex['st'], dtype=float)
                    if binding['uv_method'] == 'clamp_negative':
                        values = (*np.maximum(st, 0), 1)
                    elif binding['uv_method'] == 'tile_affine':
                        values = (*st, *np.floor(st / 1024), 1)
                    else:
                        values = (*st, 1)
                    uv = np.array(values) @ np.array(binding['uv_affine'])
                    coords.append(tuple(float(c) for c in uv))
                    refs.append((index[point], len(coords)))
                points = [np.asarray(vertices[v - 1]) for v, _uv in refs]
                # The authored engine imports clockwise faces; outward flat
                # normals are the negative cross product (as in its bridge).
                normal = -np.cross(points[1] - points[0], points[2] - points[0])
                magnitude = np.linalg.norm(normal)
                if magnitude < 1e-12:
                    raise ValueError('Degenerate static actor face: ' + recipe['model'])
                normals.append(tuple(float(c) for c in normal / magnitude))
                faces.append((binding['material'], 'f ' + ' '.join(
                    f'{v}/{t}/{len(normals)}' for v, t in refs)))
        if len(faces) != recipe['face_count']:
            raise ValueError('Static actor face selection is incomplete: ' + recipe['model'])
        tokens = [_texture_tokens(name, image) for name, image in images.items()]
        for b in {b['material']: b for b in recipe['bindings']}.values():
            culling = " nocull" if b.get("nocull") else ""
            tokens.append(f"newmtl {b['material']}{culling} map_Kd {b['texture']}")
        tokens.extend('v ' + ' '.join(_fmt(float(c)) for c in v) for v in vertices)
        tokens.extend('vt ' + ' '.join(_fmt(c) for c in uv) for uv in coords)
        tokens.extend('vn ' + ' '.join(_fmt(c) for c in normal) for normal in normals)
        active = None
        for material, face in faces:
            if active != material:
                tokens.append('usemtl ' + material)
                active = material
            tokens.append(face)
        restored.append((recipe['model'], ' '.join(tokens), {
            'vertex_count': len(vertices), 'model_faces': len(faces),
            'normal_count': len(normals),
            'normal_source': 'Outward flat face normals from transformed ROM triangles; one valid normal reference per face',
            'geometry_source': ('ROM billboard bounds with authored joining and affine transforms'
                                if recipe.get('full_billboard') else
                                'ROM display lists with authored affine transforms'),
            'uv_max_reference_error': max(b['uv_max_error'] for b in recipe['bindings']),
            'verified_texture_count': len(recipe['expected_texture_hashes']),
            'texture_rgb_sha256': recipe['expected_texture_hashes']}))
    return restored


def build_chomp_animation_payload(rom):
    segment = mio0(rom, 1851952)
    fields = struct.unpack_from('>6hIII', segment, 0x25160)
    if fields[4] != 20:
        raise ValueError('Unexpected Chain Chomp animation length')
    values, indices = fields[6] & 0xFFFFFF, fields[7] & 0xFFFFFF
    def curve(channel):
        count, first = struct.unpack_from('>HH', segment, indices + channel * 4)
        if count != 20:
            raise ValueError('Unexpected Chain Chomp jaw channel')
        samples = struct.unpack_from('>20h', segment, values + first * 2)
        return [round(((v - samples[0] + 32768) % 65536 - 32768) * 360 / 65536, 5)
                for v in samples]
    return {'Chomp.JawLowerFrames': curve(11), 'Chomp.JawUpperFrames': curve(20)}


def build_buddy_animation_payload(rom):
    """Recover the original actor's native bob and authored foot-motion curves."""
    import math
    segment = mio0(rom, 2040320)
    fields = struct.unpack_from('>6hIII', segment, 0x237FC)
    if fields[4] != 25:
        raise ValueError('Unexpected Bob-omb Buddy animation length')
    values, indices = fields[6] & 0xFFFFFF, fields[7] & 0xFFFFFF

    def channel(index):
        count, first = struct.unpack_from('>HH', segment, indices + index * 4)
        if count < 1:
            raise ValueError('Empty Bob-omb Buddy channel')
        return [struct.unpack_from('>h', segment, values + 2 * (first + min(f, count - 1)))[0]
                for f in range(25)]

    payload = {'BOB.Buddy.RootYAnim': [round((v - 175) * .00375, 8) for v in channel(1)]}
    for side, native_channel, direction in [('L', 17, 1), ('R', 29, -1)]:
        samples = channel(native_channel)
        lo, hi = min(samples), max(samples)
        if hi == lo:
            raise ValueError('Bob-omb Buddy walking channel has no motion')
        forward = [(v - (lo + hi) / 2) / (hi - lo) * .104 for v in samples]
        payload['BOB.Buddy.Foot' + side + 'Anim'] = [
            round(.015 + direction * .015 * math.sin(2 * math.pi * f / 25), 4)
            for f in range(25)]
        payload['BOB.Buddy.Foot' + side + 'Forward'] = [round(v, 6) for v in forward]
        payload['BOB.Buddy.Foot' + side + 'Lift'] = [round((.052 - abs(v)) * .5, 6)
                                                     for v in forward]
    return payload


def build_battlefield_tree_payload(rom):
    segment = mio0(rom, 4178608)
    offset = 0x10FC2
    if struct.unpack_from('>HH', segment, offset - 4) != (0x43, 17):
        raise ValueError('Unexpected BOB special-object table')
    payload = {'BOB.Tree.' + axis: [] for axis in 'XYZ'}
    for i in range(17):
        preset, x, y, z = struct.unpack_from('>Hhhh', segment, offset + i * 8)
        if preset != 121:
            raise ValueError('Unexpected BOB tree preset')
        for axis, value in zip('XYZ', (x, y, z)):
            payload['BOB.Tree.' + axis].append(value / 100)
    return payload
