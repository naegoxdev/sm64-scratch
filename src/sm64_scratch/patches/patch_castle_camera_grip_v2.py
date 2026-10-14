"""Stable V2 follow-up for the castle main door.

This is the exact scope retained after testing:
- restore the Castle Grounds camera stack that the door rewrite disconnected;
- preserve native door animation root motion while pinning Mario's logical
  position to the calibrated door anchor.

Do not fold the later experimental V3 camera/push-pull changes into this patch.
"""
from __future__ import annotations
import copy


def _target(project, name):
    return next(t for t in project['targets'] if t.get('name') == name)


def _input_refs(block, blocks):
    out=[]
    def scan(x):
        if isinstance(x,str) and x in blocks:
            out.append(x)
        elif isinstance(x,list):
            for q in x: scan(q)
        elif isinstance(x,dict):
            for q in x.values(): scan(q)
    scan(block.get('inputs',{}))
    return out


def apply_castle_camera_grip_v2(project):
    ext = project.setdefault('extensionData', {})
    if ext.get('castle_camera_grip_v2'):
        return ext['castle_camera_grip_v2']

    # 1) Restore the already-authored Castle Grounds camera placement stack.
    wt = _target(project, 'World + Assets')
    wb = wt['blocks']
    start = 'v074_cg_camx'
    if '_q' not in wb or wb['_q'].get('opcode') != 'procedures_call':
        raise RuntimeError('Grounds camera snap anchor _q not found')
    if wb['_q'].get('mutation', {}).get('proccode') != 'WORLD | snap gameplay camera':
        raise RuntimeError('Unexpected _q procedure in World + Assets')
    if start not in wb:
        raise RuntimeError('Castle Grounds camera stack v074_cg_camx not found')
    wb['_q']['next'] = start
    wb[start]['parent'] = '_q'

    # 2) Root-motion enable/disable is owned by the rewritten door state
    # machine (state 1 = on, state 2 = off), avoiding fragile block-ID hooks.

    mt = _target(project, 'Mario')
    mb = mt['blocks']

    # Remove reporter subtrees used only by the previous manual world-root
    # reconstruction. Keep aDw/aDx because the floor lock reuses them.
    old_roots=[]
    for bid in ['Bq','Br','Bs','Bt']:
        if bid not in mb:
            raise RuntimeError('Door animation root block missing: ' + bid)
        old_roots += _input_refs(mb[bid], mb)
    protected={'aDw','aDx'}
    to_remove=set()
    stack=[x for x in old_roots if x not in protected]
    while stack:
        u=stack.pop()
        if u in to_remove or u not in mb:
            continue
        to_remove.add(u)
        stack.extend([x for x in _input_refs(mb[u], mb) if x not in protected])
    for u in to_remove:
        mb.pop(u, None)

    # Match the tested V2 graph exactly: logical Mario remains at the calibrated
    # door anchor while the authored animation moves the rendered body/hand.
    mb['Bq']['opcode'] = 'data_setvariableto'
    mb['Bq']['fields'] = {'VARIABLE':['Rig.UseRootMotion','ultimate_var_333']}
    mb['Bq']['inputs'] = {'VALUE':[1,[4,1]]}
    mb['Bq'].pop('mutation', None)

    mb['Br']['opcode'] = 'data_setvariableto'
    mb['Br']['fields'] = {'VARIABLE':['SM64.PosX','ultimate_var_164']}
    mb['Br']['inputs'] = {'VALUE':[3,[12,'Door.CenterX','v017_var_417'],[10,'']]}
    mb['Br'].pop('mutation', None)

    mb['Bs']['opcode'] = 'data_setvariableto'
    mb['Bs']['fields'] = {'VARIABLE':['SM64.PosZ','ultimate_var_167']}
    mb['Bs']['inputs'] = {'VALUE':[3,[12,'Door.PlaneZ','v017_var_418'],[10,'']]}
    mb['Bs'].pop('mutation', None)

    mb['Bt']['opcode'] = 'data_setvariableto'
    mb['Bt']['fields'] = {'VARIABLE':['SM64.PosY','ultimate_var_165']}
    mb['Bt']['inputs'] = {'VALUE':[3,'aDw',[10,'']]}
    mb['Bt'].pop('mutation', None)
    if 'aDw' in mb: mb['aDw']['parent'] = 'Bt'

    if 'Bu' not in mb:
        raise RuntimeError('Door RenderY block Bu missing')
    mb['Bu']['opcode'] = 'data_setvariableto'
    mb['Bu']['fields'] = {'VARIABLE':['SM64.RenderY','ultimate_var_166']}
    mb['Bu']['inputs'] = {'VALUE':[3,'aDx',[10,'']]}
    mb['Bu'].pop('mutation', None)
    if 'aDx' in mb: mb['aDx']['parent'] = 'Bu'
    mb['Bu']['next'] = None
    mb.pop('-(', None)

    report = {
        'version': 2,
        'camera': 'restored tested Castle Grounds V2 camera stack; no V3 radial experiment',
        'door_grip': 'native door animation root motion with logical Mario pinned to calibrated door anchor',
        'scope': ['castle main door exterior camera placement', 'castle main door hand/knob trajectory'],
    }
    ext['castle_camera_grip_v2'] = report
    return report
