"""Decode the native final Lakitu flight and cameraman animation locally."""
import hashlib
import json
import math
import struct
from pathlib import Path
import numpy as np
from PIL import Image
from sm64_scratch.rom_codec import ROM_SHA1, mio0, decode_rgba16, decode_ia16
from sm64_scratch.decoders.decode_models import read_intro_logo, _texture_tokens, _fmt
from sm64_scratch.decoders.decode_animations import parse_animation
from sm64_scratch.decoders.decode_mario_poses import _sample, _rotation_xyz

from sm64_scratch.paths import METADATA_DIR


def _curve(rom, spec):
    data = rom[spec['rom_offset']:spec['rom_offset'] + spec['count'] * 8]
    if hashlib.sha256(data).hexdigest() != spec['sha256']:
        raise ValueError('Arrival spline source differs')
    return list(struct.iter_unpack('>bBhhh', data))


def build_arrival_flight_payload(rom):
    spec = json.loads((METADATA_DIR / 'arrival_flight.json').read_text())
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        raise ValueError('Unsupported arrival ROM')
    position, focus = (_curve(rom, spec[name]) for name in ('position', 'focus'))
    payload = {'Intro.Flight.' + name + '.' + axis: []
               for name in ('Camera', 'Focus', 'Lakitu') for axis in 'XYZ'}
    payload.update({'Intro.Flight.LakituPitch': [], 'Intro.Flight.LakituYaw': []})
    payload.update({'Intro.Flight.Cloud'+str(i)+'.'+axis: []
                    for i in range(5) for axis in 'XYZ'})
    segment, progress = 0, 0.0
    radius, phase, phase_speed, side, yaw, facing = 1400., -4096., 2048., -200., 32768, -16384
    lakitu_y = 560.
    for tick in range(spec['end_tick']):
        u = progress
        weights = ((1-u)**3/6, u**3/2-u*u+2/3,
                   -u**3/2+u*u/2+u/2+1/6, u**3/6)
        samples = {}
        for name, curve in (('Camera', position), ('Focus', focus)):
            raw = [sum(weights[i] * curve[segment+i][j+2] for i in range(4)) for j in range(3)]
            # The native flight reflects X/Z then adds the pipe origin.
            # Scratch's world origin cancels that origin except Z=3.
            samples[name] = [-raw[0]/100, raw[1]/100, 3-raw[2]/100]
        a, b = (1/focus[segment+i][1] if focus[segment+i][1] else 0 for i in (1, 2))
        progress += a + (b-a)*progress
        if progress >= 1:
            segment += 1
            progress -= 1
        # bhv_intro_lakitu_loop, action 3: the final orbit begins after 720.
        if tick >= 722:
            yaw = (yaw + 512) & 65535
            radius += (100-radius)*.03
            target = (yaw + 32768) & 65535
            delta = ((target-facing+32768) & 65535)-32768
            facing = (facing + int(delta / 4)) & 65535
            pitch = math.degrees(math.atan2(lakitu_y-400, 200))
            lakitu_y = 560 + 150 * math.cos(phase*math.pi/32768)
            lakitu = [2 + radius * math.sin(yaw*math.pi/32768)/100 + side/100,
                      (lakitu_y-260)/100, 3 + radius*math.cos(yaw*math.pi/32768)/100]
            phase += phase_speed
            phase_speed += (512-phase_speed)*.05
            side += (0-side)*.05
        if tick < spec['start_tick']:
            continue
        for name, point in (*samples.items(), ('Lakitu', lakitu)):
            for axis, value in zip('XYZ', point):
                payload['Intro.Flight.'+name+'.'+axis].append(round(value, 6))
        payload['Intro.Flight.LakituPitch'].append(round(pitch, 6))
        payload['Intro.Flight.LakituYaw'].append(round(facing*360/65536, 6))
        for i, height in enumerate((11,8,12,8,9)):
            scale = 4/3
            angle = (facing + (65536//5)*i)*math.pi/32768
            offset = 2*math.cos((2048*tick+16384*i)*math.pi/32768)*scale/100
            cloud = [lakitu[0]+25*scale*math.sin(angle)/100+offset,
                     lakitu[1]+scale*height/100+offset,
                     lakitu[2]+25*scale*math.cos(angle)/100+offset]
            for axis, value in zip('XYZ', cloud):
                payload['Intro.Flight.Cloud'+str(i)+'.'+axis].append(round(value,6))
    return payload, {'native_flight_ticks': [spec['start_tick'], spec['end_tick']],
                     'flight_samples': spec['end_tick']-spec['start_tick'],
                     'camera_source': 'ROM start-to-pipe spline; native reflection and pipe-origin mapping',
                     'lakitu_motion_source': 'native final orbit, action 3',
                     'pipe_rise_start_flight_frame': 25, 'pipe_rise_duration_ticks': 20}


# Animated part hierarchy and offsets are format descriptors, not a baked mesh.
# The brown camera support is a sibling of the right hand, under its shoulder
# pivot (node 5). Parenting it to the hand applies that rotation twice.
PARTS = [
    (None, (0,0,0), None), (0,(0,0,0),0x06003E30),
    (1,(132,0,0),0x06004680), (1,(87,18,72),None),
    (3,(0,0,0),0x060047E8), (1,(87,18,-72),None),
    (5,(0,0,0),0x060049E0), (6,(334,-214,-50),None),
    (7,(0,0,0),0x06005610), (8,(191,0,0),0x06005360),
    (7,(0,0,0),0x060055E8), (5,(0,0,0),0x06005598)]


def build_arrival_lakitu_model(rom):
    spec = json.loads((METADATA_DIR / 'arrival_flight.json').read_text())
    segment = mio0(rom, spec['lakitu_bank_rom_offset'])
    if hashlib.sha256(segment).hexdigest() != spec['lakitu_bank_sha256']:
        raise ValueError('Cameraman actor bank differs')
    clip = parse_animation(segment, spec['lakitu_animation_pointer'], 6, 0)
    if clip['loop_end'] != 30:
        raise ValueError('Unexpected cameraman animation length')
    roots = {i: read_intro_logo(segment, root, None, 6)[0]
             for i, (_, _, root) in enumerate(PARTS) if root}
    roots[2] += read_intro_logo(segment, 0x06004C60, None, 6)[0]
    # Native tapered cord sheets have subpixel coverage in this center-sampled
    # rasterizer. Two crossed, double-sided ribbons retain the native endpoints
    # and joint motion while preventing the cable from disappearing edge-on.
    cord_materials = set()
    for node, axis in ((8, 1), (10, 2)):
        original = roots[node][0]
        raw = [v['position'] for v in original['vertices']]
        raw = [(x, y, -nz) for x, nz, y in raw]
        # The two native sheets describe the same cable in opposite winding.
        endpoints = sorted(raw, key=lambda p: p[0])
        start = np.array(endpoints[0], dtype=float)
        end = np.mean(endpoints[1:], axis=0)
        delta = np.zeros(3); delta[axis] = 4.0
        points = [start-delta, start+delta, end+delta, end-delta]
        vertices = [{**original['vertices'][0], 'position': (p[0], -p[2], p[1]), 'st': (0, 0)}
                    for p in points]
        roots[node] = [{**original, 'vertices': [vertices[i] for i in indices]}
                       for indices in ((0, 1, 2), (0, 2, 3))]
        cord_materials.add((original['texture_pointer'], original['light_pointer']))
    images, material = {}, {}
    for triangles in roots.values():
        for tri in triangles:
            texture, light = tri['texture_pointer'], tri['light_pointer']
            key = (texture, light)
            if key in material:
                continue
            label = 'L'+str(len(material));material[key] = label
            if texture is not None:
                image = decode_rgba16(segment, texture & 0xffffff, *tri['texture_size'], 'bitrep')
                rgb = Image.new('RGB', image.size)
                rgb.putdata([(r,g,b) if a else (254,1,254) for r,g,b,a in image.getdata()])
                images[label] = rgb
            else:
                # The diffuse RGB is ROM material colour, rather than normal bytes.
                images[label] = Image.new('RGB', (1,1), tuple(segment[(light & 0xffffff)+8: (light & 0xffffff)+11]))
    vertices, coords, faces = [], [], []
    frame_counts=[]
    for frame in range(30):
        first_v, first_f = len(vertices), len(faces)
        matrices=[]
        for node, (parent, offset, _) in enumerate(PARTS):
            local=np.eye(4);local[:3,:3]=_rotation_xyz(tuple(_sample(clip,3+node*3+a,frame) for a in range(3)))
            local[:3,3]=offset
            if node==0:
                local[:3,3] += np.array([_sample(clip,a,frame) for a in range(3)])
            matrices.append(local if parent is None else matrices[parent]@local)
            for tri in roots.get(node, []):
                label=material[(tri['texture_pointer'],tri['light_pointer'])];image=images[label]
                ids=[]
                for corner in (0,2,1):
                    v=tri['vertices'][corner];x,nz,y=v['position']
                    p=matrices[node]@np.array((x,y,-nz,1.))
                    # GEO_SCALE is 1/4; OBJ importer maps x,z,-y back to engine xyz.
                    vertices.append((p[0]*.0025,-p[2]*.0025,p[1]*.0025))
                    if tri['texture_pointer'] is None:uv=(.5,.5)
                    else:uv=(v['st'][0]/(32*image.width),1-v['st'][1]/(32*image.height))
                    coords.append(uv);ids.append(len(vertices))
                faces.append((label,'f '+' '.join(f'{i}/{i}/1' for i in ids)))
        frame_counts.append((len(vertices)-first_v,len(faces)-first_f))
    if len(set(frame_counts))!=1:
        raise ValueError('Cameraman mesh frame strides differ')
    tokens=[_texture_tokens(name,image) for name,image in images.items()]
    cord_labels = {material[key] for key in cord_materials}
    tokens += [f'newmtl {name}' + (' nocull' if name in cord_labels else '') + f' map_Kd {name}' for name in images]
    tokens += ['v '+' '.join(_fmt(float(c))for c in v)for v in vertices]
    tokens += ['vt '+' '.join(_fmt(float(c))for c in uv)for uv in coords]
    active=None
    for label,face in faces:
        if label!=active:tokens.append('usemtl '+label);active=label
        tokens.append(face)
    vc,fc=frame_counts[0]
    return 'Intro Lakitu', ' '.join(tokens), {'vertex_count':len(vertices),'model_faces':len(faces),
        'animation_frames':30,'vertices_per_frame':vc,'faces_per_frame':fc,
        'source':'ROM actor display lists and native animated-part hierarchy',
        'cord_rasterization':'crossed double-sided ribbons; native endpoints retained; width 8 actor units'}


def build_arrival_cloud_model(rom):
    """Native mist quad and IA16 alpha; shader stores alpha in blue."""
    segment = mio0(rom,2102288)
    triangles = read_intro_logo(segment,0x03000880,None,3)[0]
    image = decode_ia16(segment,128,32,32)
    rgb=Image.new('RGB',image.size)
    rgb.putdata([(255,255,a) for a in image.getchannel('A').getdata()])
    tokens=[_texture_tokens('Cloud',rgb),'newmtl CloudMat nocull map_Kd Cloud']
    vertices=[];coords=[];faces=[]
    for tri in triangles:
        ids=[]
        for c in (0,2,1):
            v=tri['vertices'][c]
            vertices.append(tuple(x*.01 for x in v['position']))
            coords.append((max(0,v['st'][0])/1024,1-max(0,v['st'][1])/1024))
            ids.append(len(vertices))
        faces.append('f '+' '.join(f'{i}/{i}/1' for i in ids))
    tokens += ['v '+' '.join(_fmt(c) for c in v) for v in vertices]
    tokens += ['vt '+' '.join(_fmt(c) for c in v) for v in coords]
    tokens += ['usemtl CloudMat',*faces]
    return 'Intro Cloud',' '.join(tokens),{'model_faces':len(faces),'vertex_count':len(vertices),
        'source':'ROM mist display list and IA16 alpha'}
