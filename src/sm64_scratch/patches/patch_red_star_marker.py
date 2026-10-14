"""Add the native floor marker for BOB's eight-red-coin reward (V28)."""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _call
from sm64_scratch.patches.patch_bob_objectives import CREATE, _tail, _put


def apply_red_star_marker(project):
    meta = project.setdefault('extensionData', {})
    world = next(t for t in project['targets'] if t['name'] == 'World + Assets')
    code = 'WORLD | create BOB red star marker'
    if meta.get('red_star_marker_v28') and any(
            b.get('mutation', {}).get('proccode') == code for b in world['blocks'].values()):
        return meta['red_star_marker_v28']
    stage = next(t for t in project['targets'] if t['isStage'])
    g = Graph(world, stage)
    g.list('BOB.RedStarMarker.Placement')
    position = lambda n: g.item('BOB.RedStarMarker.Placement', n)
    g.procedure(code, [
        _call(g, CREATE, ['BOB Red Reward Marker Shadow', '3d', 'BOB Red Star Marker Shadow',
                         position(1), position(4), position(3), 0, 0, 0, '']),
        _call(g, CREATE, ['BOB Red Reward Marker', '3d', 'BOB Red Star Marker',
                         position(1), position(2), position(3), 0, 0, 0, '']),
        g.set(g.var('BOB.RedStarMarker.SceneIndex'), g.op('subtract', g.length('Scene'), 39)),
        g.call('SCENE | rebuild render queue')])
    _tail(g, 'WORLD | create BOB objectives', [g.call(code)])
    _tail(g, 'WORLD | sync BOB objectives', [
        _put(g, g.op('add', g.var('BOB.RedStarMarker.SceneIndex'), 18),
             g.op('mod', g.op('multiply', g.var('SM64.SimulationFrame'), 1.40625), 360))])
    render = next(t for t in project['targets'] if t['name'] == 'Render')
    rg = Graph(render, stage)
    name = lambda: rg.item('Loaded models', rg.op('add', rg.var('engine.modelindex'), 1))
    _tail(rg, 'RENDER | prepare Castle Grounds animated UVs', [rg.control('control_if',
        rg.op('equals', name(), 'BOB Red Star Marker'), [rg.set(rg.var('Render.FaceAlpha'), 120)])])
    # Extend the existing shadow selector without changing Mario's shadow.
    shadow_arg = rg.blocks['p10shadownum_000026']
    old = shadow_arg['inputs']['NUM1'][1]
    condition = rg.op('or', old, rg.op('equals', name(), 'BOB Red Star Marker Shadow'))
    shadow_arg['inputs']['NUM1'] = [3, condition, [10, '']]
    rg.blocks[condition]['parent'] = 'p10shadownum_000026'
    rg.insert_after('p12r_000692', [rg.control('control_if',
        rg.op('equals', name(), 'BOB Red Star Marker Shadow'),
        [rg.set(rg.var('Render.ObjectOpacity'), 155)])])
    meta['red_star_marker_v28'] = dict(version=1, native_marker=True,
        floor_relative_height=.6, native_alpha=120, shadow_alpha=155,
        mark_degrees_per_tick=1.40625, intangible=True, permanent=True,
        runtime_verified=False)
    return meta['red_star_marker_v28']
