"""Restore the visible final Lakitu flight before the native pipe camera.

Only empty runtime lists and Scratch logic are public. Motion, textures and
actor mesh frames are decoded from the user's ROM by decode_arrival_flight.
"""
import json
from sm64_scratch.patches.patch_animation import Graph


def _definition(g, code):
    return next(k for k,b in g.blocks.items() if b['opcode']=='procedures_definition'
                and g.blocks[b['inputs']['custom_block'][1]]['mutation']['proccode']==code)


def _call(g, code, values):
    proto=next(b for b in g.blocks.values() if b['opcode']=='procedures_prototype'
               and b['mutation']['proccode']==code)
    ids=json.loads(proto['mutation']['argumentids'])
    if len(ids)!=len(values):raise ValueError('Procedure argument count: '+code)
    key=g.node('procedures_call',dict(zip(ids,values)))
    g.blocks[key]['mutation']=dict(proto['mutation'])
    return key


def _replace_body(g,code,body):
    key=_definition(g,code)
    g.blocks[key]['next']=g.chain(body,key)


def _ensure_actor_models(project):
    stage=next(t for t in project['targets'] if t['isStage'])
    world=next(t for t in project['targets'] if t['name']=='World + Assets')
    g=Graph(world,stage)
    marker='arrival_flight_load_models'
    if marker in g.blocks:return
    nodes=[_call(g,'ASSET | load model %s',[name])
           for name in ('Intro Lakitu','Intro Cloud')]
    first=nodes[0]
    g.blocks[marker]=g.blocks.pop(first);nodes[0]=marker
    g.insert_after('[#',nodes)


def apply_arrival_flight(project):
    meta=project.setdefault('extensionData',{})
    stage=next(t for t in project['targets'] if t['isStage'])
    targets={t['name']:t for t in project['targets']}
    mario=targets['Mario'];g=Graph(mario,stage)
    if (meta.get('arrival_flight_fix',{}).get('version') in (1,2)
            and any(b.get('mutation',{}).get('proccode')=='INTRO | sync Lakitu flight'
                    for b in targets['World + Assets']['blocks'].values())):
        _ensure_actor_models(project)
        meta['arrival_flight_fix']['version']=2
        return meta['arrival_flight_fix']
    flight=g.var('Intro.FlightFrame');rise=g.var('Intro.RiseFrame')
    g.insert_after('+f',[g.set(flight,0)])
    g.blocks['v13_anim_Mario_34']['inputs']['CONDITION']=[2,g.op('lt',flight,g.length('Intro.Flight.Camera.X'))]
    condition=g.blocks['v13_anim_Mario_34']['inputs']['CONDITION'][1]
    g.blocks[condition]['parent']='v13_anim_Mario_34'
    advance=[g.set(flight,1,True),g.set(rise,g.op('subtract',flight,25)),
             g.control('control_if',g.op('lt',rise,0),[g.set(rise,0)]),
             g.control('control_if',g.op('gt',rise,20),[g.set(rise,20)])]
    g.blocks['v13_anim_Mario_34']['inputs']['SUBSTACK']=[2,g.chain(advance,'v13_anim_Mario_34')]
    _replace_body(g,'INTRO | ease pipe rise',[
        g.set(g.var('Intro.PipeOffset'),g.op('add',-2,g.op('multiply',rise,.1)))])
    axes=['cam x','cam y','cam z','Camera64.FocusX','Camera64.FocusY','Camera64.FocusZ']
    sample=lambda:g.op('add',flight,1)
    body=[g.set(g.var(name),g.item('Intro.Flight.'+('Camera.' if i<3 else 'Focus.')+'XYZ'[i%3],sample()))
          for i,name in enumerate(axes)]
    _replace_body(g,'INTRO | frame pipe rise from below',[
        g.control('control_if',g.op('lt',flight,g.length('Intro.Flight.Camera.X')),body)])
    # Initial render uses the complete scene at flight frame zero. Simulation
    # runs underneath the asynchronous white reveal, so Lakitu keeps moving.
    mg=Graph(targets['Main Loop'],stage);b=mg.blocks
    fade='v23_gameplay_fade_in_broadcast';parent=b[fade]['parent']
    ready=b[fade]['next'];run=b[ready]['next'];following=b[run]['next']
    if b[ready].get('fields',{}).get('VARIABLE',[None])[0]!='Arrival.Ready':
        raise ValueError('Unrecognized arrival readiness chain')
    b[parent]['next']=ready;b[ready]['parent']=parent
    b[run]['next']=fade;b[fade]['parent']=run
    b[fade]['next']=following;b[following]['parent']=fade
    b[fade]['opcode']='event_broadcast'

    world=targets['World + Assets'];wg=Graph(world,stage)
    create='SCENE | create object %s type %s model %s at %s %s %s rotation %s %s %s script %s'
    edit='SCENE | edit object %s data %s value %s operation %s'
    def set_object(name,field,value):return _call(wg,edit,[name,field,value,'set'])
    initialization=[]
    for name,model in [('Intro Lakitu','Intro Lakitu')]+[('Intro Cloud '+str(i),'Intro Cloud') for i in range(5)]:
        initialization.append(_call(wg,create,[name,'3d',model,0,-100,0,0,0,0,'']))
        if model=='Intro Cloud':
            for axis in ('sx','sy','sz'):initialization.append(set_object(name,axis,4/3))
    wg.insert_after('[#',initialization)
    _ensure_actor_models(project)
    index=lambda:wg.op('add',wg.var('Intro.FlightFrame'),1)
    visible=[];hide=[]
    for name,prefix in [('Intro Lakitu','Lakitu')]+[('Intro Cloud '+str(i),'Cloud'+str(i)) for i in range(5)]:
        visible.append(set_object(name,'type','3d'))
        for axis in 'XYZ':visible.append(set_object(name,axis.lower(),wg.item('Intro.Flight.'+prefix+'.'+axis,index())))
        if name=='Intro Lakitu':
            visible += [set_object(name,'rx',wg.item('Intro.Flight.LakituPitch',index())),
                        set_object(name,'ry',wg.item('Intro.Flight.LakituYaw',index())),
                        set_object(name,'temp',wg.op('mod',wg.var('Intro.FlightFrame'),30))]
        else:
            visible += [set_object(name,'rx',wg.var('cam rx')),set_object(name,'ry',wg.var('cam ry')),
                        set_object(name,'temp',255)]
        hide.append(set_object(name,'type','hidden'))
    # Skip all work after despawning, including the scene name lookups.
    alive=wg.var('Intro.LakituVisible')
    wg.insert_after('[#',[wg.set(alive,1)])
    guard=wg.op('and',wg.op('equals',wg.var('Intro.Active'),1),
                wg.op('lt',wg.var('Intro.FlightFrame'),wg.length('Intro.Flight.Camera.X')))
    wg.procedure('INTRO | sync Lakitu flight',[
        wg.control('control_if',wg.op('equals',wg.var('Level.ID'),'GROUNDS'),[
            wg.control('control_if_else',guard,visible,
                       [wg.control('control_if',wg.op('equals',alive,1),hide+[wg.set(alive,0)])])])])
    definition=_definition(wg,'WORLD | sync visuals')
    wg.insert_after(definition,[wg.call('INTRO | sync Lakitu flight')])
    # Animated geometry lives in one loaded mesh. Advance the face/vertex
    # ranges instead of evaluating all 30 frames every rendered image.
    for target in (targets['Render'],targets['Title Intro Render']):
        rg=Graph(target,stage)
        model=lambda:rg.item('Loaded models',rg.op('add',rg.var('engine.modelindex'),1))
        vc=lambda:rg.item('Intro.Flight.MeshStride',1)
        fc=lambda:rg.item('Intro.Flight.MeshStride',2)
        animation=rg.var('Render.AnimFrame')
        nodes=[rg.control('control_if',rg.op('equals',model(),'Intro Lakitu'),[
            rg.set(rg.var('draw first vertex'),rg.op('add',rg.var('draw first vertex'),rg.op('multiply',animation,vc()))),
            rg.set(rg.var('draw vertex count'),vc()),
            rg.set(rg.var('draw first face'),rg.op('add',rg.var('draw first face'),rg.op('multiply',animation,fc()))),
            rg.set(rg.var('draw face count'),fc())]),
            rg.control('control_if',rg.op('equals',model(),'Intro Cloud'),[
                rg.set(rg.var('Render.BlendMode'),1),rg.set(rg.var('Render.ObjectOpacity'),255)])]
        rg.insert_after('p12r_000692',nodes)
    patch_arrival_flight_audio(project)
    result=dict(version=2,flight_ticks=90,source='ROM final start-to-pipe spline and cameraman actor',
                pipe_rise_start_tick=25,pipe_rise_ticks=20,white_reveal='complete render queue, then asynchronous fade over moving Lakitu',
                pipe_camera='native height restored; native shot cut retained',
                gameplay_camera='existing v0.85 controller, warmed before release')
    meta['arrival_flight_fix']=result
    meta['garden_arrival_fix'].update(visible_pipe_rise_ticks=20,pre_pipe_flight_ticks=90)
    return result


def patch_arrival_flight_audio(project):
    """The audio constructor can regenerate this observer after graph migration."""
    stage=next(t for t in project['targets'] if t['isStage'])
    audio=next((t for t in project['targets'] if t['name']=='Gameplay Audio'),None)
    if audio is None:return
    g=Graph(audio,stage)
    for key,block in list(g.blocks.items()):
        if block['opcode']!='control_if':continue
        child=block.get('inputs',{}).get('SUBSTACK',[None,None])[1]
        writes=[]
        while child in g.blocks:
            writes.append(g.blocks[child]);child=g.blocks[child].get('next')
        if not any(b.get('fields',{}).get('VARIABLE',[None])[0]=='Audio.PipeRisePlayed'
                   and b.get('inputs',{}).get('VALUE',[None,[None,None]])[1]==[10,'1'] for b in writes):continue
        # Do not wrap an already-migrated condition again.
        marker='arrival_flight_audio_'+key
        if marker in g.blocks:continue
        old=block['inputs']['CONDITION'][1]
        condition=g.op('and',old,g.op('not',g.op('lt',g.var('Intro.FlightFrame'),25)))
        g.blocks[marker]=g.blocks.pop(condition)
        for value in g.blocks[marker]['inputs'].values():
            if isinstance(value[1],str) and value[1] in g.blocks:g.blocks[value[1]]['parent']=marker
        block['inputs']['CONDITION']=[2,marker];g.blocks[marker]['parent']=key
