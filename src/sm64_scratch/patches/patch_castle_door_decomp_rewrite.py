"""Rebuild the main castle door iris/warp timeline from the SM64 sequence.

This patch is intentionally scoped to the main entrance-door transition. It
keeps legacy reveal behaviour for non-door paths such as STAR_RETURN.
"""
from __future__ import annotations

from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition, _call, _replace_body

def target(project,name):
    return next(t for t in project['targets'] if t['name']==name)


def input_literal(block, key, value):
    """Replace a Scratch input by a numeric/text literal, preserving numeric type where useful."""
    if isinstance(value,(int,float)):
        block['inputs'][key]=[1,[4,value]]
    else:
        block['inputs'][key]=[1,[10,str(value)]]


def apply_castle_door_decomp_rewrite(project):
    meta=project.setdefault('extensionData',{})
    stage=next(t for t in project['targets'] if t.get('isStage'))
    mario=target(project,'Mario')
    world=target(project,'World + Assets')
    render=target(project,'Render')
    wipe=target(project,'Door Transition')

    # ------------------------------------------------------------------
    # 1) Mario door warp: one deterministic timer owns both iris phases.
    #    Mirrors SM64's warp-door timeline: door action reaches tick 16,
    #    20-tick fade-into-circle, warp, then 16-tick fade-from-circle.
    # ------------------------------------------------------------------
    g=Graph(mario,stage)
    forward=g.var('SM64.ForwardVel'); vx=g.var('SM64.VelX'); vy=g.var('SM64.VelY'); vz=g.var('SM64.VelZ')
    py=g.var('SM64.PosY'); ry=g.var('SM64.RenderY')
    floor=g.var('Door.FloorY'); state=g.var('Door.State'); timer=g.var('Door.Timer'); pp=g.var('Door.PushPull')
    curve=g.var('Door.CurveIndex'); angle=g.var('Door.Angle'); iris=g.var('Door.IrisFrame')
    level=g.var('Level.ID'); req=g.var('Level.Request'); closed=g.var('Door.ClosedFrameShown')
    active=g.var('Door.ActiveName'); reveal=g.var('Door.RevealActive')
    rootmotion=g.var('Rig.UseRootMotion')

    state1=[
        g.set(rootmotion,1),
        g.control('control_if_else', g.op('equals',pp,1),
                  [_call(g,'ANIM | request %s loop %s speed %s',['Open door and walk in',0,1])],
                  [_call(g,'ANIM | request %s loop %s speed %s',['Exit door',0,1])]),
        g.set(curve,g.op('add',timer,1)),
        g.control('control_if',g.op('gt',curve,g.length('Door.PullOpenCurve')),[g.set(curve,g.length('Door.PullOpenCurve'))]),
        g.control('control_if_else',g.op('equals',pp,1),
                  [g.set(angle,g.item('Door.PullOpenCurve',curve))],
                  [g.set(angle,g.item('Door.PushOpenCurve',curve))]),
        # 20 rendered closing ticks: timer 16..35 maps to Circle 01..20.
        g.control('control_if',g.op('gt',timer,15),[
            g.set(iris,g.op('subtract',timer,15)),
            g.control('control_if',g.op('gt',iris,20),[g.set(iris,20)])
        ]),
        # Warp only after Circle 20 was actually presented in the previous frame.
        g.control('control_if',g.op('and',g.op('gt',timer,35),g.op('equals',closed,1)),[
            g.set(iris,20),
            g.control('control_if_else',g.op('equals',level,'GROUNDS'),
                      [g.set(req,'LOBBY')],[g.set(req,'GROUNDS')])
        ])
    ]

    state2=[
        g.set(rootmotion,0),
        _call(g,'ANIM | request %s loop %s speed %s',['Other Standing Normal Part 3',1,1]),
        g.set(curve,g.op('add',timer,1)),
        g.control('control_if',g.op('gt',curve,g.length('Door.PullCloseCurve')),[g.set(curve,g.length('Door.PullCloseCurve'))]),
        g.control('control_if_else',g.op('equals',pp,1),
                  [g.set(angle,g.item('Door.PullCloseCurve',curve))],
                  [g.set(angle,g.item('Door.PushCloseCurve',curve))]),
        # Warp frame itself is rendered fully black; the next 16 simulation ticks reveal it.
        g.set(iris,g.math('round',g.op('subtract',20,g.op('multiply',g.op('add',timer,1),1.25)))),
        g.control('control_if',g.op('lt',iris,0),[g.set(iris,0)]),
        # Door choreography may continue after the 16-tick iris; release at its old end.
        g.control('control_if',g.op('gt',timer,39),[
            g.set(angle,0),g.set(iris,0),g.set(state,0),g.set(active,''),
            g.set(closed,0),g.set(reveal,0),
            _call(g,'SM64 STATE | set action %s arg %s',['ACT_IDLE',0]),
            _call(g,'ANIM | request %s loop %s speed %s',['Other Standing Normal Part 3',1,1])
        ])
    ]

    new_door_body=[
        g.set(forward,0),g.set(vx,0),g.set(vy,0),g.set(vz,0),
        g.set(py,g.op('add',floor,.1)),g.set(ry,g.op('add',floor,.1)),
        g.control('control_if_else',g.op('equals',state,1),state1,state2),
        g.set(timer,1,change=True)
    ]
    _replace_body(g,'SM64 DOOR | update warp action',new_door_body)

    # Reset all v13 presentation latches when a real entrance-door warp starts.
    # The existing check procedure remains responsible for choosing side/push/pull.
    scene_presented=g.var('Door.ScenePresented'); scene_warmup=g.var('Door.SceneWarmup'); reveal_start=g.var('Door.RevealStart')
    start_anchor='-t'  # existing Door.IrisFrame = 0 in the stable baseline
    if start_anchor not in g.blocks:
        raise RuntimeError('Stable door-start anchor not found')
    g.insert_after(start_anchor,[
        g.set(reveal,0),g.set(closed,0),g.set(scene_presented,0),g.set(scene_warmup,0),g.set(reveal_start,-1)
    ])

    # ------------------------------------------------------------------
    # 2) Door Transition sprite: for a real castle door (ActiveName nonempty)
    #    show exactly Door.IrisFrame. No real-time timer and no warmup/cache.
    #    Preserve the old branch untouched for STAR_RETURN (ActiveName empty).
    # ------------------------------------------------------------------
    wg=Graph(wipe,stage)
    wstate=wg.var('Door.State'); wactive=wg.var('Door.ActiveName'); wiris=wg.var('Door.IrisFrame')
    wmask=wg.var('Door.MaskFrame'); wclosed=wg.var('Door.ClosedFrameShown'); wirisdrawn=wg.var('Door.IrisDrawn')
    wdef=_definition(wg,'WIPE | present door circle')
    legacy_first=wg.blocks[wdef]['next']
    if not legacy_first:
        raise RuntimeError('Door wipe legacy body missing')
    door_active=wg.op('and',wg.op('not',wg.op('equals',wactive,'')),wg.op('gt',wstate,0))
    door_draw=[
        wg.set(wmask,wiris),
        wg.control('control_if_else',wg.op('gt',wmask,0),[
            wg.node('motion_gotoxy',{'X':0,'Y':0}),
            wg.node('looks_setsizeto',{'SIZE':100}),
            wg.node('looks_gotofrontback',fields={'FRONT_BACK':['front',None]}),
            wg.node('looks_switchcostumeto',{'COSTUME':wg.op('add',wmask,1)}),
            wg.node('looks_seteffectto',{'VALUE':0},fields={'EFFECT':['GHOST',None]}),
            wg.node('looks_show'),
            wg.set(wirisdrawn,wmask),
            wg.control('control_if_else',wg.op('equals',wmask,20),[wg.set(wclosed,1)],[wg.set(wclosed,0)])
        ],[
            wg.node('looks_hide'),wg.set(wirisdrawn,0),wg.set(wclosed,0)
        ])
    ]
    wrapper=wg.node('control_if_else',{'CONDITION':door_active})
    wg.blocks[wrapper]['inputs']['SUBSTACK']=[2,wg.chain(door_draw,wrapper)]
    wg.blocks[wrapper]['inputs']['SUBSTACK2']=[2,legacy_first]
    wg.blocks[legacy_first]['parent']=wrapper
    wg.blocks[wrapper]['parent']=wdef
    wg.blocks[wdef]['next']=wrapper

    # ------------------------------------------------------------------
    # 3) Renderer: live scene under a real castle-door iris, exactly as SM64
    #    renders the area under its screen transition. Preserve the old cache
    #    behavior only for legacy non-door reveals (notably STAR_RETURN).
    # ------------------------------------------------------------------
    rg=Graph(render,stage)
    rdef=_definition(rg,'RENDER | draw scene or reuse wipe image')
    legacy_render=rg.blocks[rdef]['next']
    if not legacy_render:
        raise RuntimeError('Render cache body missing')
    ractive=rg.var('Door.ActiveName'); rstate=rg.var('Door.State')
    real_door=rg.op('and',rg.op('not',rg.op('equals',ractive,'')),rg.op('gt',rstate,0))
    live=rg.call('RENDER | draw scene')
    rwrap=rg.node('control_if_else',{'CONDITION':real_door})
    rg.blocks[rwrap]['inputs']['SUBSTACK']=[2,rg.chain([live],rwrap)]
    rg.blocks[rwrap]['inputs']['SUBSTACK2']=[2,legacy_render]
    rg.blocks[legacy_render]['parent']=rwrap
    rg.blocks[rwrap]['parent']=rdef
    rg.blocks[rdef]['next']=rwrap

    # ------------------------------------------------------------------
    # 4) Warp initialization: remove the asynchronous reveal flag for normal
    #    entrance doors. Keep STAR_RETURN untouched. On GROUNDS, delete the
    #    second camera override: reset/snap camera exactly once before reveal.
    # ------------------------------------------------------------------
    wb=world['blocks']
    for key in ('K#','Lk'):
        if key not in wb or wb[key].get('fields',{}).get('VARIABLE',[None])[0]!='Door.RevealActive':
            raise RuntimeError(f'Expected door reveal assignment {key} not found')
        input_literal(wb[key],'VALUE',0)

    # The stable branch calls WORLD | snap gameplay camera at _q, then v0.74
    # immediately overwrites that reset with a side-offset camera. That makes
    # the first exterior reveal frame disagree with subsequent camera updates.
    if '_q' not in wb or wb['_q'].get('opcode')!='procedures_call':
        raise RuntimeError('Grounds camera snap anchor not found')
    if wb['_q'].get('mutation',{}).get('proccode')!='WORLD | snap gameplay camera':
        raise RuntimeError('Unexpected _q procedure')
    detached=wb['_q'].get('next')
    wb['_q']['next']=None
    if detached in wb:
        wb[detached]['parent']=None  # unreachable diagnostic remnant; no longer part of the substack

    meta['castle_door_decomp_rewrite']={
        'version':1,
        'base':'MARIO64_SCRATCH_BOB_FAST_OBJECT_CREATE_TEST.sb3',
        'door_close_start_tick':16,
        'iris_close_ticks':20,
        'iris_open_ticks':16,
        'closed_frame_ack':True,
        'door_render':'live scene under iris',
        'camera_reset':'single WORLD | snap gameplay camera; removed post-snap GROUNDS override',
        'legacy_reveal_fallback':'preserved for non-door STAR_RETURN path',
    }
    return project


