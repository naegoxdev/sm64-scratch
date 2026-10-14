"""Decode the native red-coin reward marker, floor placement and shadow."""
import struct
import numpy as np
from PIL import Image
from sm64_scratch.rom_codec import mio0, decode_ia8
from sm64_scratch.decoders.decode_models import read_intro_logo, _texture_tokens, _fmt
from sm64_scratch.decoders.decode_collisions import parse_collision, _triangle_values
from sm64_scratch.decoders.decode_bob_expansion import _floor


def build_red_star_marker_payload(rom):
    offset = 0x405C78
    if rom[offset:offset+4] != bytes.fromhex('24183f00'):
        raise ValueError('Unexpected BOB red-coin reward object command')
    x, y, z = [n / 100 for n in struct.unpack_from('>hhh', rom, offset+4)]
    segment = mio0(rom, 4178608)
    vertices, triangles, _ = parse_collision(segment)
    floor = _floor([_triangle_values(vertices, t) for t in triangles], x, y, z)
    # bhvRedCoinStarMarker: DROP_TO_FLOOR, then ADD_FLOAT(oPosY, 60).
    return {'BOB.RedStarMarker.Placement': [x, floor+.6, z, floor+.01]}


def build_red_star_marker_models(rom):
    segment = mio0(rom, 2102288)
    triangles, _ = read_intro_logo(segment, 0x0302C620, None, 3)
    if len(triangles) != 20:
        raise ValueError('Unexpected native transparent-star mesh')
    color = tuple(segment[0x2C490:0x2C493])
    image = Image.new('RGB', (1, 1), color)
    tokens = [_texture_tokens('RedRewardMarkerBlue', image),
              'newmtl RedRewardMarkerMaterial map_Kd RedRewardMarkerBlue',
              'usemtl RedRewardMarkerMaterial']
    for face, triangle in enumerate(triangles):
        points = []
        for vertex in [triangle['vertices'][i] for i in (0, 2, 1)]:
            x, neg_z, y = vertex['position']
            # GEO_SCALE .25, behavior XY scale 1.5, Z scale .75,
            # native pitch +90 degrees. Bake the pitch into OBJ Z-up.
            point = (x*.00375, -y*.00375, neg_z*.001875)
            points.append(np.asarray(point))
            tokens.extend(['v '+' '.join(_fmt(v) for v in point), 'vt .5 .5'])
        normal = -np.cross(points[1]-points[0], points[2]-points[0])
        normal /= np.linalg.norm(normal)
        tokens.append('vn '+' '.join(_fmt(float(v)) for v in normal))
        tokens.append('f '+' '.join(f'{face*3+i}/{face*3+i}/{face+1}' for i in (1, 2, 3)))
    marker = ('BOB Red Star Marker', ' '.join(tokens), dict(model_faces=20,
        vertex_count=60, native_alpha=120, native_scale_xy=1.5, native_scale_z=.75,
        geometry_source='US ROM transparent star, native scale and pitch baked into mesh'))
    # US segment-2 IA8 quarter-circle. Mirror across both axes as the native
    # shadow display list does, storing coverage in the existing blue channel.
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
    text = (_texture_tokens('RedRewardMarkerShadow', shadow) +
        ' newmtl RedRewardMarkerShadowMaterial nocull map_Kd RedRewardMarkerShadow '
        'v -.75 -.75 0 v .75 -.75 0 v .75 .75 0 v -.75 .75 0 '
        'vt 0 0 vt 1 0 vt 1 1 vt 0 1 vn 0 0 1 usemtl RedRewardMarkerShadowMaterial '
        'f 1/1/1 3/3/1 2/2/1 f 1/1/1 4/4/1 3/3/1')
    return [marker, ('BOB Red Star Marker Shadow', text, dict(model_faces=2,
        vertex_count=4, native_alpha=155, geometry_source='Native circular shadow footprint',
        texture_source='US segment-2 IA8 quarter-circle, mirrored into coverage map'))]
