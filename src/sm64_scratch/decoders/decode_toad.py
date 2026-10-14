"""Decode Toad's seven-bone mesh and calibrated lighting atlas from the ROM."""
import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image
from sm64_scratch.rom_codec import mio0, decode_rgba16
from sm64_scratch.decoders.decode_models import read_intro_logo, _texture_tokens, _fmt

from sm64_scratch.paths import METADATA_DIR


def _rgb_convert(array, table):
    colors, indices = np.unique(array.reshape(-1, 3), axis=0, return_inverse=True)
    try:
        mapped = np.array([table[bytes(c).hex()] for c in colors], dtype='uint8')
    except KeyError as error:
        raise ValueError('Toad atlas has an uncalibrated ROM color') from error
    return mapped[indices].reshape(array.shape)


def build_toad_atlas(rom):
    recipe = json.loads((METADATA_DIR / 'toad_atlas.json').read_text())
    segment = mio0(rom, recipe['segment_rom_offset'])
    base = np.full((256, 256, 3), (255, 0, 255), dtype='uint8')
    for i, pointer in enumerate(recipe['palette_sources']):
        # The authored atlas converts the ROM's linear light colors to sRGB.
        color = []
        for channel in segment[pointer:pointer + 3]:
            linear = channel / 255
            srgb = 12.92 * linear if linear <= .0031308 else 1.055 * linear ** (1 / 2.4) - .055
            color.append(round(srgb * 255))
        base[:128, i * 16:(i + 1) * 16] = color
    for row in recipe['textures']:
        width, height = row['image_size']
        src = decode_rgba16(segment, row['pointer'], 32, 32, 'floor')
        tile = np.array(src.convert('RGB').resize((width, height), Image.Resampling.NEAREST))
        tile = np.pad(tile, ((0, 128 - height), (0, 128 - width), (0, 0)), mode='edge')
        # The imported face tile clamps its last row up to the wrap seam;
        # the remaining area uses its first corner color.
        if row['wrap_seam_y'] > height:
            tile[row['wrap_seam_y']:] = tile[0, 0]
        tile = _rgb_convert(tile, row['rgb_lut'])
        x, y = row['origin']
        base[y:y + 128, x:x + 128] = tile
    atlas = np.concatenate([base, *(_rgb_convert(base, lut)
                                   for lut in recipe['shade_rgb_luts'])], axis=0)
    if hashlib.sha256(atlas.tobytes()).hexdigest() != recipe['expected_rgb_sha256']:
        raise ValueError('Toad atlas differs from the private reference hash')
    return Image.fromarray(atlas)


def build_toad_model(rom):
    recipe = json.loads((METADATA_DIR / 'toad.json').read_text())
    segment = mio0(rom, recipe['segment_rom_offset'])
    image = build_toad_atlas(rom)
    vertices, coords, normals, faces, vertex_bones, face_bones = [], [], [], [], [], []
    # The renderer indexes the bone arrays against this generated model;
    # keep both arrays aligned even if vertex ordering differs from the import.
    for part in sorted(recipe['parts'], key=lambda p: [6, 5, 7, 1, 4, 3, 2].index(p['bone'])):
        matrix = np.array(part['affine_transform'])
        index = {}
        bindings = {(b['texture_pointer'], b['light_pointer']): b for b in part['bindings']}
        for triangle in read_intro_logo(segment, part['root_pointer'], None, 6)[0]:
            binding = bindings[(triangle['texture_pointer'], triangle['light_pointer'])]
            refs = []
            for corner in part['corner_order']:
                v = triangle['vertices'][corner]
                point = tuple(np.array((*v['position'], 1)) @ matrix)
                if point not in index:
                    index[point] = len(vertices) + 1
                    vertices.append(point)
                    vertex_bones.append(part['bone'])
                st = [max(0, a) for a in v['st']] if binding.get('clamp_negative_st') else v['st']
                uv = np.array((*st, 1)) @ np.array(binding['uv_affine'])
                coords.append(tuple(round(float(a), 5) for a in uv))
                refs.append((index[point], len(coords)))
            points = np.array([vertices[v - 1] for v, _t in refs])
            normal = np.cross(points[2] - points[0], points[1] - points[0])
            length = np.linalg.norm(normal)
            if length < 1e-12:
                raise ValueError('Degenerate Toad triangle')
            normals.append(tuple(normal / length))
            faces.append('f ' + ' '.join(f'{v}/{t}/{len(normals)}' for v, t in refs))
            face_bones.append(part['bone'])
    if len(faces) != 316:
        raise ValueError(f'Toad bone layout differs: {len(vertices)} vertices / {len(faces)} faces')
    text = ' '.join([_texture_tokens('ToadAtlas', image), 'newmtl Mat map_Kd ToadAtlas',
                     *('v ' + ' '.join(_fmt(float(c)) for c in v) for v in vertices),
                     *('vt ' + ' '.join(_fmt(c) for c in uv) for uv in coords),
                     *('vn ' + ' '.join(_fmt(float(c)) for c in n) for n in normals),
                     'usemtl Mat', *faces])
    return ('Castle Toad', text, {'vertex_count': len(vertices), 'model_faces': len(faces),
            'texture_rgb_sha256': hashlib.sha256(image.tobytes()).hexdigest(),
            'geometry_source': 'ROM display lists with authored affine bind transforms'}), {
                'ToadRig.VertexBone': vertex_bones, 'ToadRig.FaceBone': face_bones}


def _axis_rotation(axis, radians):
    import math
    c, s = math.cos(radians), math.sin(radians)
    if axis == 0:
        return np.array(((1, 0, 0), (0, c, -s), (0, s, c)))
    if axis == 1:
        return np.array(((c, 0, s), (0, 1, 0), (-s, 0, c)))
    return np.array(((c, -s, 0), (s, c, 0), (0, 0, 1)))


def _native_rotation(angles):
    import math
    parts = [_axis_rotation(i, a * 2 * math.pi / 65536) for i, a in enumerate(angles)]
    return parts[0] @ parts[1] @ parts[2]


def _import_rotation(matrix):
    """Preserve the authored ZXY extraction followed by Blender XYZ playback."""
    import math
    x = math.asin(max(-1., min(1., -float(matrix[1, 2]))))
    y = math.atan2(matrix[0, 2], matrix[2, 2])
    z = math.atan2(matrix[1, 0], matrix[1, 1])
    return _axis_rotation(2, z) @ _axis_rotation(1, -y) @ _axis_rotation(0, -x)


def build_toad_animation_payload(rom):
    """Decode all native channels; no baked Blender animation is required."""
    from sm64_scratch.decoders.decode_animations import parse_animation
    from sm64_scratch.decoders.decode_mario_poses import _sample
    recipe = json.loads((METADATA_DIR / 'toad_animation.json').read_text())
    clip = parse_animation(mio0(rom, recipe['segment_rom_offset']),
                           recipe['animation_pointer'], 6, 0)
    if clip['loop_end'] != recipe['native_frames']:
        raise ValueError('Unexpected native Toad frame count')
    rest = np.array(recipe['rest_matrices'])
    inverse = np.linalg.inv(rest)
    localrest = [r if parent is None else inverse[parent] @ r
                 for r, parent in zip(rest, recipe['parents'])]
    origin = np.array(recipe['export_origin'])
    initial = {row['native_joint']: _native_rotation(tuple(
        _sample(clip, 3 + row['native_joint'] * 3 + a, 0) for a in range(3)))
        for row in recipe['rotation_channels']}
    payload = []
    for export_frame in range(recipe['export_frames']):
        # The imported loop contains one closing copy of the first frame.
        frame = export_frame % recipe['native_frames']
        rotations = {}
        for row in recipe['rotation_channels']:
            node = row['native_joint']
            native = _native_rotation(tuple(_sample(clip, 3 + node * 3 + a, frame)
                                            for a in range(3)))
            basis = np.array(row['basis'])
            rotations[row['rig_joint']] = _import_rotation(
                basis @ native @ initial[node].T @ basis.T)
        posed = []
        for node, parent in enumerate(recipe['parents']):
            channel = np.eye(4)
            if node in rotations:
                channel[:3, :3] = rotations[node]
            if node == recipe['root_joint']:
                source = recipe['root_channel']
                channel[1, 3] = (_sample(clip, source, frame) - _sample(clip, source, 0)) * recipe['root_scale']
            local = localrest[node] @ channel
            posed.append(local if parent is None else posed[parent] @ local)
        for node in recipe['render_joints']:
            skin = posed[node] @ inverse[node]
            skin[:3, 3] = (skin[:3, 3] * recipe['export_scale'] +
                           (np.eye(3) - skin[:3, :3]) @ origin)
            payload.extend(round(float(value), 6) for value in skin[:3].ravel())
    if len(payload) != 27 * 7 * 12 or not np.isfinite(payload).all():
        raise ValueError('Invalid Toad pose layout')
    # Scratch's loader replaces items in place; an empty scratch buffer
    # would discard every write and collapse the rig. Seed the first pose.
    return {'ToadRig.PoseData': payload, 'ToadRig.CurrentMatrix': payload[:84]}
