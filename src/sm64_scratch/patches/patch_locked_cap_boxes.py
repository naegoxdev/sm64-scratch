"""Assetless locked Wing Cap boxes, with native outlines and rotating marks."""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _call
from sm64_scratch.patches.patch_bob_objectives import CREATE, _tail, _put


def apply_locked_cap_boxes(project):
    meta = project.setdefault('extensionData', {})
    world = next(t for t in project['targets'] if t['name'] == 'World + Assets')
    code = 'WORLD | create locked BOB cap boxes'
    if meta.get('locked_cap_boxes_v27') and any(
            b.get('mutation', {}).get('proccode') == code for b in world['blocks'].values()):
        return meta['locked_cap_boxes_v27']
    stage = next(t for t in project['targets'] if t['isStage'])
    g = Graph(world, stage)
    i = g.var('BOB.LockedCap.Index', local=True)
    for field in ('X', 'Y', 'Z', 'Yaw', 'MarkIndex'):
        g.list('BOB.LockedCap.' + field)
    join = lambda prefix: g.node('operator_join', {'STRING1': prefix, 'STRING2': i})
    setup = [g.node('data_deletealloflist', fields={'LIST': g.list('BOB.LockedCap.MarkIndex')}),
             g.set(i, 1)]
    body = []
    for part in ('Panels', 'Outline', 'Mark'):
        body.append(_call(g, CREATE, [join('BOB Locked Cap ' + part + ' '), '3d',
            'BOB Locked Wing Cap ' + part, *[g.item('BOB.LockedCap.' + a, i) for a in 'XYZ'],
            0, g.item('BOB.LockedCap.Yaw', i), 0, '']))
        if part == 'Mark':
            body.append(g.node('data_addtolist', {'ITEM': g.op('subtract', g.length('Scene'), 39)},
                               {'LIST': g.list('BOB.LockedCap.MarkIndex')}))
    body.append(g.set(i, 1, True))
    setup.extend([g.control('control_repeat', g.length('BOB.LockedCap.X'), body),
                  g.call('SCENE | rebuild render queue')])
    g.procedure(code, setup)
    _tail(g, 'WORLD | create BOB objectives', [g.call(code)])
    sync = [g.set(i, 1), g.control('control_repeat', g.length('BOB.LockedCap.MarkIndex'), [
        _put(g, g.op('add', g.item('BOB.LockedCap.MarkIndex', i), 18),
             g.op('mod', g.op('add', g.item('BOB.LockedCap.Yaw', i),
                              g.op('multiply', g.var('SM64.SimulationFrame'), 11.25)), 360)),
        g.set(i, 1, True)])]
    g.procedure('WORLD | rotate locked BOB cap marks', sync)
    _tail(g, 'WORLD | sync BOB objectives', [g.call('WORLD | rotate locked BOB cap marks')])
    # Existing alpha-texture path already handles colored translucent water.
    # Apply the box's native 80/255 alpha only to its panel mesh; the keyed
    # border and punctuation remain opaque. No global opacity changes.
    render = next(t for t in project['targets'] if t['name'] == 'Render')
    rg = Graph(render, stage)
    model = rg.item('Loaded models', rg.op('add', rg.var('engine.modelindex'), 1))
    _tail(rg, 'RENDER | prepare Castle Grounds animated UVs', [rg.control('control_if',
        rg.op('equals', model, 'BOB Locked Wing Cap Panels'), [rg.set(rg.var('Render.FaceAlpha'), 80)])])
    meta['locked_cap_boxes_v27'] = dict(version=1, native_placements=3, locked=True,
        intangible=True, breakable=False, cap_spawn=False, mark_degrees_per_tick=11.25,
        panels_alpha=80, runtime_verified=False)
    return meta['locked_cap_boxes_v27']
