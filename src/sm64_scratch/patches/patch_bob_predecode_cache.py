"""Predecode BOB-only models into Scratch runtime geometry lists.

The engine's ASSET parser tokenizes each model string character-by-character and
then scans the token list three times.  BOB's first entry otherwise parses ~2 MB
of model text while the painting transition is fully white.  This patch stores
BOB-only static model geometry/textures in the project lists and preserves that
baseline across the renderer reset, so ASSET | load model sees them as already
loaded.
"""
from __future__ import annotations
import math
from sm64_scratch.patches.patch_animation import Graph


BOB_MODELS = [
    'Bob-omb Battlefield',
    'Power Star', 'Power Star Collected',
    'Chain Chomp Lower', 'Chain Chomp Upper', 'Chain Chomp Link', 'Chain Chomp Post',
    'BOB Chain Chomp Gate',
    'BOB Cannon Lid', 'BOB Wooden Bridge', 'BOB Breakable Box', 'BOB Small Box',
    'BOB Checkerboard Lift', 'BOB Bob-omb Buddy', 'BOB Cannon Base', 'BOB Cannon Barrel',
    'BOB Buddy Foot L', 'BOB Buddy Foot R', 'BOB Buddy Key',
    'BOB Buddy Eyes Open', 'BOB Buddy Eyes Blink',
    'Red Coin Frame 1', 'Red Coin Frame 2', 'Red Coin Frame 3', 'Red Coin Frame 4',
    'Red Coin Number 1', 'Red Coin Number 2', 'Red Coin Number 3', 'Red Coin Number 4',
    'Red Coin Number 5', 'Red Coin Number 6', 'Red Coin Number 7',
    'BOB Goomba Body', 'BOB Goomba Head Open', 'BOB Goomba Head Blink',
    'BOB Goomba Foot L', 'BOB Goomba Foot R', 'BOB Goomba Death Puff',
    'BOB Cannon Opening',
    'BOB Yellow Coin Frame 1', 'BOB Yellow Coin Frame 2',
    'BOB Yellow Coin Frame 3', 'BOB Yellow Coin Frame 4',
    'BOB Black Bob-omb Body',
]

BASELINE_LISTS = (
    'vx','vy','vz','vx2','vy2','vz2',
    'f1','f2','f3','nx','ny','nz',
    'u1','v1','u2','v2','u3','v3','start','width','height',
    'Render.FaceTwoSided','Render.MaterialTwoSided',
    'texture','texture names','texture width','texture height','texture length',
    'mtl','mtl tex',
    'Loaded models','model first vertex','model vertex count','model first face','model face count',
)


def _stage_list(stage, name):
    matches = [v for v in stage['lists'].values() if v[0] == name]
    if len(matches) != 1:
        raise ValueError(f'Missing/duplicate Stage list {name!r}: {len(matches)}')
    return matches[0][1]


def _parse_model_source(text):
    """Read just the commands consumed by ASSET | parse OBJ data."""
    tok = text.split(); i = 0
    vertices=[]; texcoords=[]; textures=[]; materials=[]; faces=[]
    current_material = None
    material_index = {}
    while i < len(tok):
        cmd = tok[i]; i += 1
        if cmd == 'o': i += 1
        elif cmd == 'v':
            vertices.append(tuple(float(x) for x in tok[i:i+3])); i += 3
        elif cmd == 'vt':
            texcoords.append(tuple(float(x) for x in tok[i:i+2])); i += 2
        elif cmd == 'vn': i += 3  # ignored by Scratch parser
        elif cmd == 'newtex':
            name = tok[i]; w=int(tok[i+1]); h=int(tok[i+2]); i += 3
            pixels=[]
            for _ in range(w*h):
                if tok[i] != 'c': raise ValueError(f'Expected c in texture {name}, got {tok[i]}')
                if len(tok[i+1]) == 6:
                    pixels.append(int(tok[i+1], 16)); i += 2
                else:
                    r,g,b=(float(tok[i+1]),float(tok[i+2]),float(tok[i+3]))
                    pixels.append(int(65536*r + 256*g + b)); i += 4
            textures.append((name,w,h,pixels))
        elif cmd == 'newmtl':
            name=tok[i]; i+=1
            material_index[name]=len(materials)
            materials.append({'name':name,'texture':'','two_sided':0})
        elif cmd == 'nocull':
            if not materials: raise ValueError('nocull before newmtl')
            materials[-1]['two_sided']=1
        elif cmd == 'map_Kd':
            if not materials: raise ValueError('map_Kd before newmtl')
            materials[-1]['texture']=tok[i]; i+=1
        elif cmd == 'usemtl':
            name=tok[i];i+=1
            if name not in material_index: raise ValueError(f'Unknown material {name}')
            current_material=material_index[name]
        elif cmd == 'f':
            refs=tok[i:i+3];i+=3
            parsed=[]
            for ref in refs:
                parts=ref.split('/')
                vi=int(parts[0]); ti=int(parts[1]) if len(parts)>1 and parts[1] else 0
                parsed.append((vi,ti))
            if current_material is None: raise ValueError('face before usemtl')
            faces.append((parsed,current_material))
        elif cmd in ('Ka','Kd','Ks','Ke'): i += 3
        elif cmd in ('Ns','Ni','d','Tr','illum'): i += 1
        else:
            raise ValueError(f'Unknown model token {cmd!r}')
    return vertices,texcoords,textures,materials,faces


def _normal(a,b,c):
    ux,uy,uz=(b[0]-a[0],b[1]-a[1],b[2]-a[2])
    vx,vy,vz=(c[0]-a[0],c[1]-a[1],c[2]-a[2])
    x=uy*vz-uz*vy; y=uz*vx-ux*vz; z=ux*vy-uy*vx
    d=math.sqrt(x*x+y*y+z*z)
    return (0.0,0.0,0.0) if d == 0 else (x/d,y/d,z/d)


def _append_model(stage, model_name, text):
    V,T,textures,materials,faces=_parse_model_source(text)
    vx=_stage_list(stage,'vx');vy=_stage_list(stage,'vy');vz=_stage_list(stage,'vz')
    vx2=_stage_list(stage,'vx2');vy2=_stage_list(stage,'vy2');vz2=_stage_list(stage,'vz2')
    f1=_stage_list(stage,'f1');f2=_stage_list(stage,'f2');f3=_stage_list(stage,'f3')
    nx=_stage_list(stage,'nx');ny=_stage_list(stage,'ny');nz=_stage_list(stage,'nz')
    u1=_stage_list(stage,'u1');v1=_stage_list(stage,'v1');u2=_stage_list(stage,'u2');v2=_stage_list(stage,'v2');u3=_stage_list(stage,'u3');v3=_stage_list(stage,'v3')
    starts=_stage_list(stage,'start');widths=_stage_list(stage,'width');heights=_stage_list(stage,'height')
    tex_pixels=_stage_list(stage,'texture');tex_names=_stage_list(stage,'texture names')
    tex_w=_stage_list(stage,'texture width');tex_h=_stage_list(stage,'texture height');tex_start=_stage_list(stage,'texture length')
    mtl=_stage_list(stage,'mtl');mtl_tex=_stage_list(stage,'mtl tex')
    mat2=_stage_list(stage,'Render.MaterialTwoSided');face2=_stage_list(stage,'Render.FaceTwoSided')
    loaded=_stage_list(stage,'Loaded models');mfv=_stage_list(stage,'model first vertex');mvc=_stage_list(stage,'model vertex count');mff=_stage_list(stage,'model first face');mfc=_stage_list(stage,'model face count')

    first_vertex=len(vx)+1;first_face=len(f1)+1
    # Scratch coordinate conversion: x, source-z, -source-y.
    for x,y,z in V:
        vx.append(x);vy.append(z);vz.append(-y)
        vx2.append('');vy2.append('');vz2.append('')

    tex_lookup={}
    for local,w,h,pix in textures:
        full=model_name+local
        tex_lookup[local]=(len(tex_names)+1,w,h,len(tex_pixels)+1)
        tex_names.append(full);tex_w.append(w);tex_h.append(h);tex_start.append(len(tex_pixels)+1)
        tex_pixels.extend(pix)

    mat_global=[]
    for row in materials:
        mtl.append(model_name+row['name'])
        mtl_tex.append(model_name+row['texture'] if row['texture'] else '')
        mat2.append(row['two_sided'])
        mat_global.append(len(mtl))

    for refs,mi in faces:
        # Scratch reverses source vertices 2 and 3.
        r1,r2,r3=refs[0],refs[2],refs[1]
        gi1=first_vertex+r1[0]-1;gi2=first_vertex+r2[0]-1;gi3=first_vertex+r3[0]-1
        p1=(vx[gi1-1],vy[gi1-1],vz[gi1-1]);p2=(vx[gi2-1],vy[gi2-1],vz[gi2-1]);p3=(vx[gi3-1],vy[gi3-1],vz[gi3-1])
        nn=_normal(p1,p2,p3)
        tex_local=materials[mi]['texture']
        if tex_local not in tex_lookup: raise ValueError(f'{model_name}: material texture {tex_local!r} not defined in same model')
        _ti,w,h,st=tex_lookup[tex_local]
        def uv(ref):
            if ref[1] < 1 or ref[1] > len(T): return (0.0,0.0)
            return T[ref[1]-1]
        t1,t2,t3=uv(r1),uv(r2),uv(r3)
        f1.append(gi1);f2.append(gi2);f3.append(gi3)
        nx.append(nn[0]);ny.append(nn[1]);nz.append(nn[2])
        u1.append(t1[0]*w);v1.append(t1[1]*h)
        u2.append(t2[0]*w);v2.append(t2[1]*h)
        u3.append(t3[0]*w);v3.append(t3[1]*h)
        starts.append(st);widths.append(w);heights.append(h)
        face2.append(materials[mi]['two_sided'])

    if not loaded:
        loaded.append(0)
    if loaded[0] != len(loaded)-1:
        raise ValueError('Loaded models counter inconsistent before predecode')
    loaded[0] += 1;loaded.append(model_name)
    mfv.append(first_vertex);mvc.append(len(V));mff.append(first_face);mfc.append(len(faces))


def _unlink(blocks,key):
    b=blocks[key];parent=b.get('parent');nxt=b.get('next')
    if parent and parent in blocks:
        p=blocks[parent]
        if p.get('next')==key:p['next']=nxt
        else:
            for inp in p.get('inputs',{}).values():
                if isinstance(inp,list) and len(inp)>1 and inp[1]==key:
                    inp[1]=nxt;break
            else: raise ValueError(f'Cannot unlink {key}')
    if nxt and nxt in blocks:blocks[nxt]['parent']=parent
    del blocks[key]


def _patch_reset_target(target,stage,baseline_lengths,model_count):
    g=Graph(target,stage)
    # One atomic trim restores the immutable BOB cache while discarding models
    # appended by the previous run. First project launch does zero iterations.
    body=[]
    for name in BASELINE_LISTS:
        count=baseline_lengths[name]
        body.append(g.control('control_repeat',g.op('subtract',g.length(name),count),[
            g.node('data_deleteoflist',{'INDEX':'last'},{'LIST':g.list(name)})]))
    body.append(g.set(g.var('last model id'),model_count))
    proc='ASSET | restore BOB predecoded baseline'
    g.procedure(proc,body)

    # Find the renderer reset procedure from its known vx clear and insert trim
    # just before the first list reset. Then remove clears for preserved lists.
    blocks=g.blocks
    clear_keys=[]
    for key,b in list(blocks.items()):
        if b.get('opcode')=='data_deletealloflist':
            name=b.get('fields',{}).get('LIST',[None])[0]
            if name in BASELINE_LISTS:clear_keys.append(key)
    if not clear_keys: raise ValueError(f'{target["name"]}: no renderer cache clears found')
    # Locate earliest clear by walking backwards from any preserved clear to procedure definition.
    candidates=[]
    for key in clear_keys:
        cur=key;dist=0
        while blocks.get(cur,{}).get('parent') and blocks[blocks[cur]['parent']].get('opcode')!='procedures_definition':
            cur=blocks[cur]['parent'];dist+=1
        if blocks.get(cur,{}).get('parent') and blocks[blocks[cur]['parent']].get('opcode')=='procedures_definition':
            candidates.append((dist,cur,blocks[cur]['parent']))
    if not candidates: raise ValueError(f'{target["name"]}: cache reset procedure not found')
    # The list reset chain is the large one containing many preserved clears.
    # Its first statement is reached from the common procedure definition.
    _,_,definition=max(candidates)
    first=blocks[definition].get('next')
    call=g.call(proc)
    blocks[definition]['next']=call;blocks[call]['parent']=definition;blocks[call]['next']=first
    if first:blocks[first]['parent']=call

    for key in list(clear_keys):
        if key in blocks:_unlink(blocks,key)
    # Existing reset adds a zero counter immediately after clearing Loaded models.
    # With a baseline cache, that would corrupt [count, names...].
    for key,b in list(blocks.items()):
        if b.get('opcode')=='data_addtolist' and b.get('fields',{}).get('LIST',[None])[0]=='Loaded models':
            item=b.get('inputs',{}).get('ITEM')
            if item==[1,[4,'0']]:_unlink(blocks,key)


def _restore_model_counter(target, stage, model_count):
    """Keep the model ID counter aligned with the retained metadata rows.

    Trimming model names does not trim the counter stored in their first item.
    A stale counter makes the next decoded model return an out-of-range ID.
    """
    g = Graph(target, stage)
    marker = 'bob_cache_model_counter_reset'
    if marker in g.blocks:
        return
    definition = next(k for k, b in g.blocks.items()
                      if b['opcode'] == 'procedures_definition'
                      and g.blocks[b['inputs']['custom_block'][1]]['mutation']['proccode']
                      == 'ASSET | restore BOB predecoded baseline')
    last = definition
    while g.blocks[last].get('next'):
        last = g.blocks[last]['next']
    node = g.node('data_replaceitemoflist', {'INDEX': 1, 'ITEM': model_count},
                  {'LIST': g.list('Loaded models')})
    g.blocks[marker] = g.blocks.pop(node)
    g.insert_after(last, [marker])


def apply_bob_predecode_cache(project):
    meta=project.setdefault('extensionData',{})
    if meta.get('bob_predecode_cache_v1'):
        report = meta['bob_predecode_cache_v1']
        if report.get('version', 1) < 2:
            stage = next(t for t in project['targets'] if t.get('isStage'))
            for name in ('Render', 'Title Intro Render'):
                target = next(t for t in project['targets'] if t['name'] == name)
                _restore_model_counter(target, stage, report['models_predecoded'])
            report.update(version=2, counter_reset='restore Loaded models[1] with its retained names and metadata')
        return report
    stage=next(t for t in project['targets'] if t.get('isStage'))
    pre=_stage_list(stage,'Pre-loaded')
    records={pre[i][1:]:pre[i+1] for i in range(0,len(pre),2) if isinstance(pre[i],str) and pre[i].startswith('+')}
    missing=[n for n in BOB_MODELS if n not in records]
    if missing:raise ValueError('Missing BOB model sources: '+', '.join(missing))
    # Runtime lists must still be pristine in this build before inserting cache.
    dirty=[name for name in BASELINE_LISTS if _stage_list(stage,name)]
    if dirty:raise ValueError('Runtime lists already populated before BOB predecode: '+', '.join(dirty[:8]))
    _stage_list(stage,'Loaded models').append(0)
    for name in BOB_MODELS:_append_model(stage,name,records[name])
    baseline={name:len(_stage_list(stage,name)) for name in BASELINE_LISTS}
    for target_name in ('Render','Title Intro Render'):
        target=next(t for t in project['targets'] if t.get('name')==target_name)
        _patch_reset_target(target,stage,baseline,len(BOB_MODELS))
        _restore_model_counter(target,stage,len(BOB_MODELS))
    report={
        'version':2,'models_predecoded':len(BOB_MODELS),
        'vertices':baseline['vx'],'faces':baseline['f1'],'texture_pixels':baseline['texture'],
        'textures':baseline['texture names'],'materials':baseline['mtl'],
        'purpose':'remove first-entry runtime OBJ tokenization/three-pass parsing from BOB painting load',
        'counter_reset':'restore Loaded models[1] with its retained names and metadata',
    }
    meta['bob_predecode_cache_v1']=report
    return report
