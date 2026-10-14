"""Reconstruct garden actor meshes and their authored frame layouts locally."""
import hashlib
import json
import math
import struct
from pathlib import Path
import numpy as np
from PIL import Image
from sm64_scratch.rom_codec import mio0
from sm64_scratch.decoders.decode_models import read_intro_logo, _texture_tokens, _fmt

from sm64_scratch.paths import METADATA_DIR


def _sample_animation(segment, pointer):
    fields = struct.unpack_from('>6hIII', segment, pointer)
    values, index = fields[6] & 0xFFFFFF, fields[7] & 0xFFFFFF
    channels = struct.unpack_from('>' + str((fields[5] + 1) * 6) + 'H', segment, index)
    def sample(channel, frame):
        count, first = channels[channel * 2:channel * 2 + 2]
        return struct.unpack_from('>h', segment, values + 2 * (first + min(frame, count - 1)))[0]
    return sample, fields[4]


def _serialize(texture, image, vertices, coords, faces, nocull=True):
    return ' '.join([_texture_tokens(texture, image),
        f'newmtl Mat{" nocull" if nocull else ""} map_Kd {texture}',
        *('v ' + ' '.join(_fmt(float(c)) for c in v) for v in vertices),
        *('vt ' + ' '.join(_fmt(float(c)) for c in uv) for uv in coords),
        'usemtl Mat', *faces])


def build_butterfly_model(rom):
    segment = mio0(rom, 2102288)
    raw = np.frombuffer(segment[17320:17320 + 4096], dtype='>u2').reshape(64, 32)
    rgb = np.stack([(raw >> 11) & 31, (raw >> 6) & 31, (raw >> 1) & 31], axis=2).astype('uint8') * 8
    rgb[(raw & 1) == 0] = (254, 1, 254)
    # The original importer pads a 32-wide texture to 64 with clamped edge
    # columns on each side of its wrap seam. Preserve that atlas layout.
    atlas = np.concatenate([rgb, np.repeat(rgb[:, -1:, :], 17, axis=1),
                            np.repeat(rgb[:, :1, :], 15, axis=1)], axis=1)
    expected = 'cef83c67101f1da1c278a6ff7de4d4ab2c707ff1814e2215e7befafb862566f9'
    if hashlib.sha256(atlas.tobytes()).hexdigest() != expected:
        raise ValueError('Butterfly atlas differs from the reference hash')
    image = Image.fromarray(atlas)
    vertices, coords, faces = [], [], []
    frames = 0
    for clip in (0x55B0, 0x5698):
        sample, frame_count = _sample_animation(segment, clip)
        frames += frame_count
        for frame in range(frame_count):
            for root, channel in ((0x03005408, 8), (0x030054A0, 17)):
                angle = sample(channel, frame) * math.pi / 32768
                index = {}
                for triangle in read_intro_logo(segment, root, None, 3)[0]:
                    ids = []
                    for corner in (0, 2, 1):
                        vertex = triangle['vertices'][corner]
                        x, y, z = vertex['position']
                        point = (x * math.cos(angle) * .0025,
                                 x * math.sin(angle) * .0025, -y * .0025)
                        if point not in index:
                            index[point] = len(vertices) + 1
                            vertices.append(point)
                        coords.append((vertex['st'][0] / 2048, 1 - vertex['st'][1] / 4096))
                        ids.append((index[point], len(coords)))
                    faces.append('f ' + ' '.join(f'{v}/{t}/1' for v, t in ids))
    if (len(vertices), len(faces), frames) != (304, 152, 38):
        raise ValueError('Butterfly animation stride differs from the Scratch renderer')
    return 'Castle Butterfly', _serialize('Tex', image, vertices, coords, faces), {
        'vertex_count': len(vertices), 'model_faces': len(faces), 'animation_frames': frames,
        'texture_rgb_sha256': expected, 'geometry_source': 'two ROM animations, 8 and 30 frames'}


def build_bird_model(rom):
    recipe = json.loads((METADATA_DIR / 'bird.json').read_text())
    segment = mio0(rom, recipe['segment_rom_offset'])
    colors = [tuple(segment[p + 8:p + 11]) for p in (0, 48, 24)]
    colors += [tuple(segment[p:p + 3]) for p in (0, 48, 24)] + [(255, 255, 255)] * 2
    image = Image.new('RGB', (32, 4))
    image.putdata([colors[x // 4] for y in range(4) for x in range(32)])
    if hashlib.sha256(image.tobytes()).hexdigest() != recipe['expected_rgb_sha256']:
        raise ValueError('Bird palette differs from the reference hash')
    vertices, coords, faces = [], [], []
    # The private reference contains seven identical mesh frames. Bird motion
    # is handled by its preserved Scratch actor logic; do not substitute a
    # different ROM clip merely because these imported mesh frames repeat.
    for frame in range(recipe['frames']):
        for part in recipe['parts']:
            triangles = read_intro_logo(segment, part['root_pointer'], None, 5)[0]
            matrix = np.array(part['affine_transform'])
            for back in (False, True):
                index = {}
                for triangle in triangles:
                    ids = []
                    color = {0: 0, 24: 2, 48: 1}[triangle['light_pointer'] & 0xFFFFFF] + (3 if back else 0)
                    for corner in ((0, 1, 2) if part['root_pointer'] in (0x05000670, 0x05000598) else (0, 2, 1)):
                        vertex = triangle['vertices'][corner]
                        point = tuple(np.array((*vertex['position'], 1)) @ matrix)
                        key = (point, color)
                        if key not in index:
                            index[key] = len(vertices) + 1
                            vertices.append(point)
                        coords.append(((color * 4 + 1) / 32, .75))
                        ids.append((index[key], len(coords)))
                    faces.append('f ' + ' '.join(f'{v}/{t}/1' for v, t in ids))
    if (len(vertices), len(faces)) != (784, 798):
        raise ValueError('Bird frame stride differs from the Scratch renderer')
    return 'Castle Bird', _serialize('Tex', image, vertices, coords, faces), {
        'vertex_count': len(vertices), 'model_faces': len(faces), 'animation_frames': recipe['frames'],
        'texture_rgb_sha256': hashlib.sha256(image.tobytes()).hexdigest(),
        'geometry_source': 'ROM display lists and authored affine bind transforms',
        'known_limitations': []}


def build_flag_model(rom):
    """Bake the native flag joints into the reference's 29-frame mesh layout."""
    from sm64_scratch.decoders.decode_mario_poses import _rotation_xyz
    segment = mio0(rom, 4500416)
    sample, frame_count = _sample_animation(segment, 0xC944)
    parts = ((0x0700C768, 0), (0x0700C728, 459), (0x0700C6E8, 460),
             (0x0700C6A8, 345), (0x0700C670, 287))
    axis = np.eye(4)
    axis[:3, :3] = ((1, 0, 0), (0, 0, -1), (0, 1, 0))
    vertices, faces = [], []
    # The authored import omits the geo root's fixed rotation/translation;
    # the preserved scene logic places the flag in world space instead.
    for frame in range(frame_count):
        pose, index = np.eye(4), {}
        for node, (root, offset) in enumerate(parts):
            local = np.eye(4)
            local[0, 3] = offset
            local[:3, :3] = _rotation_xyz(tuple(
                sample(6 + node * 3 + c, frame) for c in range(3)))
            pose = pose @ local
            for triangle in read_intro_logo(segment, root, None, 7)[0]:
                material = 'WhiteMat' if triangle['light_pointer'] == 0x0700C4E0 else 'RedMat'
                ids = []
                for corner in (0, 2, 1):
                    v = triangle['vertices'][corner]
                    point = tuple((axis @ pose @ axis.T @ np.array((*v['position'], 1)))[:3] * .00375)
                    # Native buffer boundaries preserve coincident seam and
                    # material vertices. The runtime advances by 23, not by
                    # the number of unique spatial positions in each frame.
                    key = (node, v['source_address'])
                    if key not in index:
                        index[key] = len(vertices) + 1
                        vertices.append(point)
                    ids.append(index[key])
                faces.append((material, 'f ' + ' '.join(f'{i}/1/1' for i in ids)))
    if (frame_count, len(vertices), len(faces)) != (29, 667, 290):
        raise ValueError('Flag frame stride differs from the Scratch renderer')
    tokens, hashes = [], {}
    # Preserve the two material gains of the authored Scratch import. The
    # native diffuse colours alone are brighter than these reference tints.
    colors = (('Red', 0xC4C8, 224,
               'b8fe27513bb00a1e6e53a71123e6b7d1837c8e753ebe34bcf53eaed2f3739c68'),
              ('White', 0xC4E0, 216,
               '48455f6320abebd52e813837270573a651129b84c8cfcd2d297060417b165700'))
    for name, pointer, gain, expected in colors:
        image = Image.new('RGB', (1, 1), tuple(
            round(c * gain / 255) for c in segment[pointer + 8:pointer + 11]))
        hashes[name] = hashlib.sha256(image.tobytes()).hexdigest()
        if hashes[name] != expected:
            raise ValueError('Flag material differs from its reference hash')
        tokens.extend((_texture_tokens(name, image), f'newmtl {name}Mat nocull map_Kd {name}'))
    tokens.extend('v ' + ' '.join(_fmt(float(c)) for c in v) for v in vertices)
    tokens.append('vt 0 0')
    active = None
    for material, face in faces:
        if material != active:
            tokens.append('usemtl ' + material)
            active = material
        tokens.append(face)
    return 'Castle Flag', ' '.join(tokens), {
        'vertex_count': len(vertices), 'model_faces': len(faces),
        'animation_frames': frame_count, 'texture_rgb_sha256': hashes,
        'geometry_source': 'ROM flag animation and display lists; authored root omission and scale'}


def build_fish_model(rom, verified_textures):
    """Preserve the original project's repeated bind-pose fish mesh frames."""
    segment = mio0(rom, 2102288)
    image = dict(verified_textures)['Tex']
    expected = '1f24ebf5f98ce99079bb576f35ca16202602662ca79c75098dd36d13be5456b6'
    if hashlib.sha256(image.tobytes()).hexdigest() != expected:
        raise ValueError('Fish texture differs from the reference hash')
    vertices, coords, faces = [], [], []
    # All 30 imported reference frames are identical. Native skeletal motion
    # would change this model; preserve its existing Scratch swimming logic.
    for frame in range(30):
        index = {}
        for root, tail_offset in ((0x0301BFB8, 0), (0x0301C150, 97)):
            for triangle in read_intro_logo(segment, root, None, 3)[0]:
                ids = []
                for corner in (0, 2, 1):
                    v = triangle['vertices'][corner]
                    x, y, z = v['position']
                    point = (-z * .0025 - .04, -y * .0025, -(x + tail_offset) * .0025)
                    key = (root, v['source_address'])
                    if key not in index:
                        index[key] = len(vertices) + 1
                        vertices.append(point)
                    coords.append((v['st'][0] / 1024, 1 - v['st'][1] / 1024))
                    ids.append((index[key], len(coords)))
                faces.append('f ' + ' '.join(f'{v}/{t}/1' for v, t in ids))
    if (len(vertices), len(faces)) != (690, 1140):
        raise ValueError('Fish frame stride differs from the Scratch renderer')
    return 'Castle Fish', _serialize('Tex', image, vertices, coords, faces, nocull=False), {
        'vertex_count': len(vertices), 'model_faces': len(faces), 'animation_frames': 30,
        'texture_rgb_sha256': expected,
        'geometry_source': 'ROM bind vertices and tail pivot; authored axis conversion and repeated frames'}


def build_garden_actor_runtime(rom):
    grounds = json.loads((METADATA_DIR / 'grounds.json').read_text())
    translation = grounds['world_translation']
    def position(offset):
        if rom[offset - 4:offset - 2] != bytes.fromhex('2418'):
            raise ValueError('Expected a ROM level-script object command')
        return [value / 100 + delta for value, delta in
                zip(struct.unpack_from('>hhh', rom, offset), translation)]
    birds = [position(0x4548B8 + i * 24) for i in range(3)]
    butterflies = [position(0x4549B0 + i * 24) for i in range(12)]
    segment = mio0(rom, grounds['segments']['7']['rom_offset'])
    for offset in (0x11656, 0x11660):
        preset, x, y, z, parameter = struct.unpack_from('>HhhhH', segment, offset)
        if preset != 120:
            raise ValueError('Expected a butterfly triplet macro')
        butterflies.extend([[v / 100 + d for v, d in zip((x, y, z), translation)]] * 3)
    payload = {}
    for rows, prefix in ((birds, 'Grounds.Bird.GroupHome'), (butterflies, 'Grounds.Butter.Home')):
        for axis, column in zip('XYZ', range(3)):
            payload[prefix + axis] = [row[column] for row in rows]
    # Actor types and phase staggering are authored engine initialization.
    payload['Grounds.Butter.Type'] = [0] * 12 + [1] * 6
    payload['Grounds.Butter.Phase'] = [(i * 11) % 97 for i in range(18)]
    payload['Grounds.Bird.GroupYaw'] = json.loads((METADATA_DIR / 'bird.json').read_text())['authored_initial_heading_degrees']
    flags = [position(0x45494C + i * 24) for i in range(4)]
    for axis, column in zip('XYZ', range(3)):
        payload['Grounds.Flag.Home' + axis] = [row[column] for row in flags]
    payload['Grounds.Flag.Phase'] = [i * 7 for i in range(4)]
    # These are the authored swimming school initialization curves. Native
    # fish vertices/pixels still come from the ROM; no sampled pose is stored.
    payload['Grounds.Fish.Phase'] = [(i * 29) % 360 for i in range(20)]
    payload['Grounds.Fish.Radius'] = [2 + (i % 6) * .75 for i in range(20)]
    payload['Grounds.Fish.Speed'] = [1.6 + (i % 5) * .35 for i in range(20)]
    payload['Grounds.Fish.Depth'] = [1.2 + (i % 7) * .9 for i in range(20)]
    payload['Grounds.Fish.Scale'] = [.82 + (i % 5) * .09 for i in range(20)]
    return payload
