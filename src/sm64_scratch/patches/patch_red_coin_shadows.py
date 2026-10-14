"""Restore the native circular shadows under BOB red coins."""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _call
from sm64_scratch.patches.patch_bob_objectives import CREATE, _tail, _put


def apply_red_coin_shadows(project):
    meta = project.setdefault('extensionData', {})
    if meta.get('red_coin_shadows_v30'):
        return meta['red_coin_shadows_v30']

    stage = next(t for t in project['targets'] if t.get('isStage'))
    world = next(t for t in project['targets'] if t['name'] == 'World + Assets')
    g = Graph(world, stage)
    g.list('BOB.RedCoin.ShadowY')
    g.list('BOB.RedCoin.ShadowSceneIndex')

    i = g.var('BOB.RedCoin.ShadowIndex', local=True)
    idx = g.var('BOB.RedCoin.ShadowObjectIndex', local=True)
    join = lambda a, b: g.node('operator_join', {'STRING1': a, 'STRING2': b})
    add = lambda name, value: g.node('data_addtolist', {'ITEM': value}, {'LIST': g.list(name)})
    clear = lambda name: g.node('data_deletealloflist', fields={'LIST': g.list(name)})

    create_body = [
        _call(g, 'ASSET | load model %s', ['BOB Red Coin Shadow']),
        clear('BOB.RedCoin.ShadowSceneIndex'),
        g.set(i, 1),
        g.control('control_repeat', g.length('BOB.RedCoin.X'), [
            _call(g, CREATE, [join('BOB Red Coin Shadow ', i), '3d', 'BOB Red Coin Shadow',
                              g.item('BOB.RedCoin.X', i), g.item('BOB.RedCoin.ShadowY', i),
                              g.item('BOB.RedCoin.Z', i), 0, 0, 0, '']),
            add('BOB.RedCoin.ShadowSceneIndex', g.op('subtract', g.length('Scene'), 39)),
            g.set(i, 1, True),
        ]),
        g.call('SCENE | rebuild render queue'),
    ]
    g.procedure('WORLD | create BOB red coin shadows', create_body)
    _tail(g, 'WORLD | create BOB objectives', [g.call('WORLD | create BOB red coin shadows')])

    sync_body = [
        g.set(i, 1),
        g.control('control_repeat', g.length('BOB.RedCoin.ShadowSceneIndex'), [
            g.set(idx, g.item('BOB.RedCoin.ShadowSceneIndex', i)),
            g.control('control_if_else', g.op('equals', g.item('BOB.RedCoin.Collected', i), 1),
                [_put(g, g.op('add', idx, 4), 'hidden')],
                [_put(g, g.op('add', idx, 4), '3d')]),
            g.set(i, 1, True),
        ])
    ]
    g.procedure('WORLD | sync BOB red coin shadows', sync_body)
    _tail(g, 'WORLD | sync BOB objectives', [g.call('WORLD | sync BOB red coin shadows')])

    # Reuse the engine's existing soft-shadow shader. The procedural texture
    # stores coverage in blue, matching Mario's shadow material convention.
    render = next(t for t in project['targets'] if t['name'] == 'Render')
    rg = Graph(render, stage)
    name = lambda: rg.item('Loaded models', rg.op('add', rg.var('engine.modelindex'), 1))

    shadow_arg = rg.blocks.get('p10shadownum_000026')
    if not shadow_arg or shadow_arg.get('opcode') != 'operator_multiply':
        raise ValueError('Red coin shadow patch: shadow selector not found')
    old = shadow_arg['inputs']['NUM1'][1]
    condition = rg.op('or', old, rg.op('equals', name(), 'BOB Red Coin Shadow'))
    shadow_arg['inputs']['NUM1'] = [3, condition, [10, '']]
    rg.blocks[condition]['parent'] = 'p10shadownum_000026'

    opacity_anchor = 'p12r_000692'
    if opacity_anchor not in rg.blocks:
        raise ValueError('Red coin shadow patch: opacity anchor not found')
    rg.insert_after(opacity_anchor, [rg.control('control_if',
        rg.op('equals', name(), 'BOB Red Coin Shadow'),
        [rg.set(rg.var('Render.ObjectOpacity'), 180)])])

    result = dict(version=1, red_coin_shadows=8, native_shape='SHADOW_CIRCLE_4_VERTS',
                  native_diameter_cm=80, native_alpha=180,
                  floor_projection='static BOB collision floor beneath each red coin',
                  runtime_verified=False)
    meta['red_coin_shadows_v30'] = result
    return result
