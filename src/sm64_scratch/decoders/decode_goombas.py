"""Decode Goomba meshes, baked walk frames and spawn macros from the US ROM."""
import hashlib
import math
import struct
from PIL import Image
import numpy as np
from sm64_scratch.rom_codec import mio0, decode_ia16
from sm64_scratch.decoders.decode_models import read_intro_logo, _texture_tokens, _fmt
from sm64_scratch.decoders.decode_mario_poses import _rotation_xyz


def _animation(segment):
    fields = struct.unpack_from('>6hIII', segment, 0x1DA34)
    if fields[4:6] != (30, 7):
        raise ValueError('Unexpected Goomba animation layout')
    values, indices = fields[6] & 0xFFFFFF, fields[7] & 0xFFFFFF
    channels = struct.unpack_from('>48H', segment, indices)

    def sample(channel, frame):
        count, first = channels[channel * 2:channel * 2 + 2]
        return struct.unpack_from('>h', segment, values + 2 * (first + min(frame, count - 1)))[0]
    return sample


def _poses(sample, frame):
    parents = [None, 0, 1, 1, 3, 1, 5]
    offsets = [(0, 0, 0), (0, 0, 0), (48, 0, 0),
               (-60, -16, 45), (0, 0, 0), (-60, -16, -45), (0, 0, 0)]
    matrices = []
    for node, offset in enumerate(offsets):
        matrix = np.eye(4)
        matrix[:3, :3] = _rotation_xyz(tuple(sample(3 + 3 * node + a, frame) for a in range(3)))
        matrix[:3, 3] = offset if node else tuple(sample(a, frame) for a in range(3))
        matrices.append(matrix if parents[node] is None else matrices[parents[node]] @ matrix)
    return matrices


def _mesh_text(texture, image, frames, deduplicate):
    vertices, coords, faces = [], [], []
    for triangles in frames:
        index = {}
        uv_index = {}
        for points, uv in triangles:
            ids = []
            for point, texcoord in zip(points, uv):
                # The existing renderer slices 37 vertices per head frame and
                # 108 vertices per foot frame. Preserve those authored strides.
                key = tuple(point)
                if not deduplicate or key not in index:
                    index[key] = len(vertices) + 1
                    vertices.append(point)
                uv_key = tuple(texcoord)
                if not deduplicate or uv_key not in uv_index:
                    uv_index[uv_key] = len(coords) + 1
                    coords.append(texcoord)
                ids.append((index[key], uv_index[uv_key]))
            faces.append('f ' + ' '.join(f'{v}/{t}/1' for v, t in ids))
    text = ' '.join([_texture_tokens(texture, image),
        f'newmtl M0 map_Kd {texture}',
        *('v ' + ' '.join(_fmt(float(c)) for c in v) for v in vertices),
        *('vt ' + ' '.join(_fmt(float(c)) for c in uv) for uv in coords),
        'usemtl M0', *faces])
    return text, {'model_faces': len(faces), 'vertex_count': len(vertices), 'uv_count': len(coords),
                  'animation_frames': len(frames),
                  'texture_rgb_sha256': hashlib.sha256(image.tobytes()).hexdigest()}


def build_goomba_models(rom, verified_by_model):
    segment = mio0(rom, 2040320)
    sample = _animation(segment)
    axis = np.eye(4)
    axis[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    scale = np.diag([.00375, .00375, .00375, 1])
    poses = [_poses(sample, frame) for frame in range(30)]
    brown = 0x194D0
    darker = 0x194E8
    colors = [(127, 127, 127), tuple(segment[brown + 8:brown + 11]),
              tuple(segment[brown:brown + 3]), tuple(segment[darker + 8:darker + 11]),
              tuple(segment[darker:darker + 3]), (255, 255, 255),
              (255, 255, 255), (255, 255, 255)]
    palette = Image.new('RGB', (32, 4))
    palette.putdata([colors[x // 4] for y in range(4) for x in range(32)])
    restored = []
    for suffix, root, node in [('Head Open', 0x0801B5C8, 2), ('Head Blink', 0x0801B5F0, 2),
                               ('Foot L', 0x0801CE20, 4), ('Foot R', 0x0801CF78, 6)]:
        name = 'BOB Goomba ' + suffix
        texture = 'G_BOB_Goomba_' + suffix.replace(' ', '_') + '_T0'
        head = suffix.startswith('Head')
        image = dict(verified_by_model[name])[texture] if head else palette
        triangles = read_intro_logo(segment, root, None, 8)[0]
        frames = []
        reflection = np.diag([1, -1, 1, 1]) if head else np.eye(4)
        for pose in poses:
            matrix = reflection @ scale @ axis @ pose[node] @ axis.T
            rows = []
            for triangle in triangles:
                order = (0, 2, 1) if head else (0, 1, 2)
                vertices = [triangle['vertices'][i] for i in order]
                points = [(matrix @ np.array((*v['position'], 1)))[:3] for v in vertices]
                uv = [(v['st'][0] / 8192, v['st'][1] / 2048) for v in vertices] if head else [
                    (.15625 if node == 4 else .40625, .25)] * 3
                rows.append((points, uv))
            frames.append(rows)
        text, report = _mesh_text(texture, image, frames, deduplicate=head)
        if head:
            text = text.replace('newmtl M0 map_Kd', 'newmtl M0 nocull map_Kd')
        report['two_sided'] = head
        expected_v, expected_f = (1110, 2100) if head else (3240, 1080)
        if (report['vertex_count'], report['model_faces'], report['uv_count']) != (expected_v, expected_f, expected_v):
            raise ValueError('Goomba frame stride differs from the renderer: ' + name)
        restored.append((name, text, report))
    name = 'BOB Goomba Body'
    texture, image = verified_by_model[name][0]
    rows = []
    for triangle in read_intro_logo(segment, 0x0801B690, None, 8)[0]:
        vertices = triangle['vertices']
        points = [(v['position'][0] * .015, 0, v['position'][2] * .015 + .28125) for v in vertices]
        rows.append((points, [tuple(st / 1024 for st in v['st']) for v in vertices]))
    text, report = _mesh_text(texture, image, [rows], deduplicate=False)
    text = text.replace('newmtl M0 map_Kd', 'newmtl M0 nocull map_Kd')
    report['two_sided'] = True
    restored.append((name, text, report))
    # The authored death billboard uses the native mist's binary alpha mask.
    alpha = decode_ia16(mio0(rom, 2102288), 128, 32, 32).getchannel('A')
    image = Image.new('RGB', (32, 32))
    image.putdata([(255, 255, 255) if value > 127 else (254, 1, 254)
                   for value in alpha.getdata()])
    expected = 'bc5b32d43b139ad34d40a78a4bc42fe5907877b14b518015a6b2995932fe4a84'
    if hashlib.sha256(image.tobytes()).hexdigest() != expected:
        raise ValueError('Goomba death puff alpha mask differs from the reference')
    points = [(-.25, 0, -.25), (.25, 0, -.25), (.25, 0, .25), (-.25, 0, .25)]
    uv = [(0, .96875), (.96875, .96875), (.96875, 0), (0, 0)]
    rows = [([points[i] for i in ids], [uv[i] for i in ids])
            for ids in ((0, 1, 2), (0, 2, 3))]
    text, report = _mesh_text('GoombaPuff', image, [rows], deduplicate=False)
    text = text.replace('newmtl M0 map_Kd', 'newmtl M0 nocull map_Kd')
    restored.append(('BOB Goomba Death Puff', text, report))
    return restored


def build_goomba_runtime(rom):
    segment = mio0(rom, 2040320)
    sample = _animation(segment)
    payload = {'BOB.Goomba.RootYAnim': [round((sample(1, f) - sample(1, 0)) * .00375, 8) for f in range(30)]}
    channels = {'BodyPitch': 6, 'BodyRoll': 7, 'HeadPitch': 9,
                'FootLPitch': 15, 'FootLYaw': 16, 'FootLRoll': 17,
                'FootRPitch': 21, 'FootRYaw': 22, 'FootRRoll': 23}
    for name, channel in channels.items():
        origin = sample(channel, 0) if name.startswith('Foot') else 0
        payload['BOB.Goomba.' + name] = [round(((sample(channel, f) - origin + 32768) % 65536 - 32768) * 360 / 65536, 5) for f in range(30)]
    for side, direction in [('L', 1), ('R', -1)]:
        wave = [direction * math.sin(f * 2 * math.pi / 30) for f in range(30)]
        payload['BOB.Goomba.Foot' + side + 'Forward'] = [round(v * .13, 6) for v in wave]
        payload['BOB.Goomba.Foot' + side + 'Lift'] = [round(max(0, v) * .09, 6) for v in wave]
    positions, spawners = [], []
    macros = mio0(rom, 4178608)
    for offset in range(0x1104C, len(macros), 10):
        preset, x, y, z, parameter = struct.unpack_from('>HhhhH', macros, offset)
        if preset == 0x001E:
            break
        preset &= 0x1FF
        if preset == 0x44:
            positions.append((x / 100, y / 100, z / 100))
            spawners.append((9999, 9999))
        elif preset == 0x3F:
            for angle in (0, 120, 240):
                positions.append((round(x / 100 + 5 * math.cos(math.radians(angle)), 4),
                                  y / 100, round(z / 100 + 5 * math.sin(math.radians(angle)), 4)))
                spawners.append((x / 100, z / 100))
    if len(positions) != 11:
        raise ValueError('Expected eleven BOB Goomba spawn positions')
    for i, axis in enumerate('XYZ'):
        payload['BOB.Goomba.Spawn' + axis] = [p[i] for p in positions]
    for i, axis in enumerate('XZ'):
        payload['BOB.Goomba.Spawner' + axis] = [p[i] for p in spawners]
    return payload
