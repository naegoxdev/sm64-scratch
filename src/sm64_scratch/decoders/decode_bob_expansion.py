"""Decode BOB formations, loot sources and local warps from the US ROM."""
import math
import struct
from PIL import Image
from sm64_scratch.rom_codec import mio0, decode_ia16, decode_rgba16
from sm64_scratch.decoders.decode_models import _texture_tokens
from sm64_scratch.decoders.decode_collisions import parse_collision, _triangle_values


def macro_rows(rom):
    segment = mio0(rom, 4178608)
    offset = 0x1104C
    while offset + 10 <= len(segment):
        word, x, y, z, parameter = struct.unpack_from('>HhhhH', segment, offset)
        offset += 10
        if word == 30:return
        yield (word & 511) - 31, (word >> 9) * 360 / 128, x, y, z, parameter


def _floor(rows, x, y, z):
    best = None
    for r in rows:
        if r['NY'] < .01 or not r['MinX'] <= x <= r['MaxX'] or not r['MinZ'] <= z <= r['MaxZ']:continue
        sides = [(r['X'+str(b)]-r['X'+str(a)])*(z-r['Z'+str(a)]) -
                 (r['Z'+str(b)]-r['Z'+str(a)])*(x-r['X'+str(a)]) for a,b in ((1,2),(2,3),(3,1))]
        if min(sides) < -1e-7 and max(sides) > 1e-7:continue
        height = -(r['NX']*x+r['NZ']*z+r['Origin'])/r['NY']
        if height <= y + 3 and (best is None or height > best):best = height
    return y if best is None else best


def build_bob_expansion_payload(rom):
    segment = mio0(rom, 4178608)
    vertices, triangles, _ = parse_collision(segment)
    floors = [_triangle_values(vertices, t) for t in triangles]
    coins, posts, boxes, bombs = [], [], [], []
    for preset, yaw, x, y, z, parameter in macro_rows(rom):
        local = []
        if preset == 0:local = [(0,0,0,False)]
        elif preset == 6:local = [(0,0,160*(i-2),True) for i in range(5)]
        elif preset == 7:local = [(300*math.sin(i*math.pi/4),0,300*math.cos(i*math.pi/4),True) for i in range(8)]
        elif preset == 12:local = [(200*math.cos(i*math.pi/4),200*math.sin(i*math.pi/4)+200,0,False) for i in range(8)]
        for dx,dy,dz,ground in local:
            a=math.radians(yaw);px=(x+dx*math.cos(a)+dz*math.sin(a))/100
            pz=(z+dz*math.cos(a)-dx*math.sin(a))/100;py=(y+dy)/100
            if ground:py=_floor(floors,px,py,pz)
            coins.append((px,py,pz))
        if preset == 106:posts.append((x/100,y/100,z/100))
        if preset in (70,72):boxes.append((x/100,y/100,z/100, 'BOB Breakable Box' if preset==70 else 'BOB Small Box'))
        if preset in (111,115):bombs.append((x/100,y/100,z/100))
    if len(coins)!=68 or len(posts)!=4 or len(boxes)!=3 or len(bombs)!=12:
        raise ValueError('Unexpected BOB formation/loot macro table')
    # The chain post also awards five coins after three complete circles.
    posts.append((2.6,7.35,19.2))
    groups = [(p,5,'POST') for p in posts] + [(p[:3],3,'BOX') for p in boxes] + [(p,1,'BOMB') for p in bombs]
    loot, starts = [], []
    for group,(position,count,kind) in enumerate(groups,1):
        starts.append(len(loot)+1)
        for n in range(count):loot.append((*position,group,n*360/count))
    result={}
    for prefix,rows,fields in [('BOB.Yellow.',coins,'XYZ'),('BOB.Post.',posts,'XYZ'),
                              ('BOB.Box.',boxes,('X','Y','Z','Model')),('BOB.Bomb.',bombs,'XYZ'),
                              ('BOB.Loot.',loot,('HomeX','HomeY','HomeZ','Group','Yaw'))]:
        for i,field in enumerate(fields):result[prefix+field]=[p[i] for p in rows]
    result['BOB.LootGroup.Start']=starts
    result['BOB.LootGroup.Count']=[p[1] for p in groups]
    # Object commands and warp-node destinations are read directly from the
    # level script. Descriptors are offsets, not embedded placement arrays.
    warps=[]
    for offset in range(0x405DB4,0x405E14,24):
        if rom[offset:offset+4] != bytes.fromhex('24181f00'):raise ValueError('BOB warp command mismatch')
        x,y,z,rx,ry,rz=struct.unpack_from('>6h',rom,offset+4)
        node=struct.unpack_from('>I',rom,offset+16)[0]>>16&255
        warps.append((x/100,y/100,z/100,ry%360,node))
    for i,field in enumerate(('X','Y','Z','Yaw','Node')):result['BOB.Warp.'+field]=[p[i] for p in warps]
    destinations=[]
    for p in warps:
        marker=bytes((0x26,8,p[4],9,1))
        offset=rom.find(marker,0x405E10,0x406000)
        if offset<0:raise ValueError('BOB local warp destination missing')
        node=rom[offset+5];destinations.append([w[4] for w in warps].index(node)+1)
    result['BOB.Warp.Destination']=destinations
    return result,dict(placed_yellow_coins=len(coins),post_coin_rewards=25,box_coin_rewards=9,
        bobomb_coin_rewards=12,existing_goomba_coin_rewards=11,total_yellow_coins=125,
        native_total_coin_value_including_red_and_koopa_blue=146,teleport_pairs=2,
        limitation='Koopa blue-coin value is separate from the 125 yellow coins; Koopa behavior remains unrestored')


def build_fast_coin_models(rom):
    segment=mio0(rom,2102288);result=[]
    for frame,offset in enumerate((0x5780,0x5F80,0x6780,0x6F80),1):
        source=decode_ia16(segment,offset,32,32)
        image=Image.new('RGB',source.size)
        image.putdata([(r,r,0) if a else (254,1,254) for r,g,b,a in source.getdata()])
        texture=f'BOBFastCoinTexture{frame}';material=f'BOBFastCoinMaterial{frame}'
        text=('o Coin v -.32 0 0 v .32 0 0 v .32 0 .64 v -.32 0 .64 '
              'vt 0 1 vt 1 1 vt 1 0 vt 0 0 vn 0 -1 0 '+_texture_tokens(texture,image)+
              f' newmtl {material} nocull map_Kd {texture} usemtl {material} '
              'f 1/1/1 3/3/1 2/2/1 f 1/1/1 4/4/1 3/3/1')
        result.append((f'BOB Yellow Coin Frame {frame}',text,dict(model_faces=2,vertex_count=4,
                      geometry_source='Native IA16 alpha-keyed billboard')))
    return result


def build_black_bobomb_model(rom):
    """Reuse decoded native buddy parts with the native black body texture."""
    segment=mio0(rom,2040320);image=Image.new('RGB',(64,64))
    for x,offset in [(0,0x1DA60),(32,0x1EA60)]:
        tile=decode_rgba16(segment,offset,32,64,'round')
        rgb=Image.new('RGB',tile.size);rgb.putdata([(r,g,b) if a else (254,1,254) for r,g,b,a in tile.getdata()]);image.paste(rgb,(x,0))
    body=('o Body v -.49 0 .16625 v .49 0 .16625 v .49 0 1.14625 v -.49 0 1.14625 '
          'vt 0 1 vt 1 1 vt 1 0 vt 0 0 vn 0 -1 0 '+_texture_tokens('BlackBombBody',image)+
          ' newmtl BlackBombBodyMat nocull map_Kd BlackBombBody usemtl BlackBombBodyMat '
          'f 1/1/1 3/3/1 2/2/1 f 1/1/1 4/4/1 3/3/1')
    # Body faces the camera independently; feet/key/eyes retain object yaw.
    return ('BOB Black Bob-omb Body',body,dict(model_faces=2,vertex_count=4,geometry_source='Native black Bob-omb texture halves'))


def build_cannon_opening_model(restored):
    """Visible well marker where the imported terrain has no recessed hole.

    Reuse the decoded lid footprint/material; only the open rim is authored.
    """
    lid=next(text for name,text,report in restored if name=='BOB Cannon Lid').split()
    points=[tuple(float(n) for n in lid[i+1:i+4]) for i,t in enumerate(lid) if t=='v']
    x0,x1=min(p[0] for p in points),max(p[0] for p in points)
    y0,y1=min(p[1] for p in points),max(p[1] for p in points)
    texture=lid[lid.index('map_Kd')+1];textures=[]
    for i,t in enumerate(lid):
        if t=='newtex':
            count=int(lid[i+2])*int(lid[i+3]);textures.extend(lid[i:i+4+2*count])
    border=min(x1-x0,y1-y0)*.09
    vertices=[(x0,y0,0),(x1,y0,0),(x1,y1,0),(x0,y1,0),
              (x0+border,y0+border,0),(x1-border,y0+border,0),
              (x1-border,y1-border,0),(x0+border,y1-border,0)]
    tokens=['o Opening',*textures,'newtex WellBlack 1 1 c 000000',
        f'newmtl WellRim nocull map_Kd {texture}','newmtl WellInterior nocull map_Kd WellBlack']
    for x,y,z in vertices:
        tokens.extend(['v',str(x),str(y),str(z)])
    for x,y,z in vertices:
        tokens.extend(['vt',str((x-x0)/(x1-x0)),str((y-y0)/(y1-y0))])
    tokens.extend(['vn','0','0','1','usemtl','WellRim'])
    for a in range(4):
        b=(a+1)%4
        for face in [(a,b,b+4),(a,b+4,a+4)]:tokens.extend(['f',*[f'{i+1}/{i+1}/1' for i in face]])
    tokens.extend(['usemtl','WellInterior','f','5/5/1','6/6/1','7/7/1','f','5/5/1','7/7/1','8/8/1'])
    return ('BOB Cannon Opening',' '.join(tokens),dict(model_faces=10,vertex_count=8,
        geometry_source='Decoded lid footprint/material with authored open-well rim for unrecessed terrain'))
