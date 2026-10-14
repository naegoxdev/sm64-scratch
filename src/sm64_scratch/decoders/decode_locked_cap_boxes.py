"""Decode the native locked Wing Cap boxes from the local US ROM."""
import hashlib
import numpy as np
from PIL import Image
from sm64_scratch.rom_codec import mio0, decode_rgba16
from sm64_scratch.decoders.decode_models import read_intro_logo, _texture_tokens, _fmt
from sm64_scratch.decoders.decode_bob_expansion import macro_rows


def build_locked_cap_box_payload(rom):
    # US macro preset 60 is macro_box_wing_cap. No placement data enters BASE.
    rows = [row for row in macro_rows(rom) if row[0] == 60]
    if len(rows) != 3 or any(row[5] for row in rows):
        raise ValueError('Unexpected BOB Wing Cap box macro records')
    return {'BOB.LockedCap.' + field: [row[column] / divisor for row in rows]
            for field, column, divisor in [('X', 2, 100), ('Y', 3, 100),
                                           ('Z', 4, 100), ('Yaw', 1, 1)]}


def build_locked_cap_box_models(rom):
    segment = mio0(rom, 2040320)
    result = []
    recipes = [('BOB Locked Wing Cap Panels', 0x08024F88, None, None),
               ('BOB Locked Wing Cap Outline', 0x080259F8, 0x25168, (32, 32)),
               ('BOB Locked Wing Cap Mark', 0x08025F08, 0x25A80, (16, 32))]
    for name, root, offset, size in recipes:
        triangles, _ = read_intro_logo(segment, root, None, 8)
        if len(triangles) != (2 if size == (16, 32) else 12):
            raise ValueError('Unexpected locked cap box display list: ' + name)
        if offset is None:
            # Diffuse red Lights1 swatch. The renderer blends panels at the
            # native vertex alpha (0x50), leaving the inside visible.
            image = Image.new('RGB', (1, 1), tuple(segment[0x24CC0:0x24CC3]))
        else:
            source = decode_rgba16(segment, offset, *size, 'round')
            rgb = Image.new('RGB', size)
            rgb.putdata([(r, g, b) if a else (254, 1, 254)
                         for r, g, b, a in source.getdata()])
            image = rgb
            if size == (16, 32):
                # Native clamp coordinates extend past both image edges.
                # Transparent padding preserves those margins without the
                # engine's repeating sampler duplicating the punctuation.
                image = Image.new('RGB', (64, 64), (254, 1, 254))
                image.paste(rgb, (16, 16))
        texture = name.replace(' ', '') + 'Texture'
        material = name.replace(' ', '') + 'Material'
        tokens = [_texture_tokens(texture, image),
                  'newmtl ' + material + (' nocull' if offset else '') +
                  ' map_Kd ' + texture, 'usemtl ' + material]
        for face, triangle in enumerate(triangles):
            points = []
            for vertex in [triangle['vertices'][i] for i in (0, 2, 1)]:
                # read_intro_logo already converts native Y-up to OBJ Z-up.
                # Both native behaviors scale their geometry to 200 percent.
                point = tuple(value * .02 for value in vertex['position'])
                points.append(np.asarray(point))
                if offset is None:
                    uv = (.5, .5)
                elif size == (16, 32):
                    uv = tuple((st / 32 + 16) / 64 for st in vertex['st'])
                else:
                    uv = tuple(st / 1024 for st in vertex['st'])
                tokens.extend(['v ' + ' '.join(_fmt(v) for v in point),
                               'vt ' + ' '.join(_fmt(v) for v in uv)])
            normal = -np.cross(points[1] - points[0], points[2] - points[0])
            normal /= np.linalg.norm(normal)
            tokens.append('vn ' + ' '.join(_fmt(float(v)) for v in normal))
            tokens.append('f ' + ' '.join(f'{face*3+i}/{face*3+i}/{face+1}'
                                          for i in (1, 2, 3)))
        result.append((name, ' '.join(tokens), dict(model_faces=len(triangles),
            vertex_count=3*len(triangles), geometry_source='US ROM native display list',
            display_list=hex(root), behavior_scale=2,
            native_panel_alpha=80 if offset is None else 255,
            texture_rgb_sha256=hashlib.sha256(image.tobytes()).hexdigest())))
    return result
