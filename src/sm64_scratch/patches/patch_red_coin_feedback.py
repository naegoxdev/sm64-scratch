"""Assetless native red-coin number feedback, revision 22.

Reuse seven hidden two-triangle scene objects. No polling thread, clones or
new HUD overlay: the normal world synchronization advances the native bounce
in simulation ticks, independently of rendering FPS.
"""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _call
from sm64_scratch.patches.patch_bob_objectives import CREATE, _tail, _put


def apply_red_coin_feedback(project):
    meta = project.setdefault('extensionData', {})
    world = next(t for t in project['targets'] if t['name'] == 'World + Assets')
    if meta.get('red_coin_feedback_v22') and any(
            b.get('mutation', {}).get('proccode') == 'WORLD | sync red coin numbers'
            for b in world['blocks'].values()):
        return meta['red_coin_feedback_v22']
    stage = next(t for t in project['targets'] if t['isStage'])
    mario = next(t for t in project['targets'] if t['name'] == 'Mario')
    wg, mg = Graph(world, stage), Graph(mario, stage)
    lists = ('SceneIndex', 'Age', 'SpawnFrame', 'Y', 'Velocity')
    setup = [wg.node('data_deletealloflist', fields={'LIST': wg.list('BOB.Number.' + name)})
             for name in lists]
    for number in range(1, 8):
        model = f'Red Coin Number {number}'
        setup.extend([
            _call(wg, 'ASSET | load model %s', [model]),
            _call(wg, CREATE, [f'BOB Collection Number {number}', 'hidden', model,
                              0, 0, 0, 0, 0, 0, ''])])
        for name, value in [('SceneIndex', wg.op('subtract', wg.length('Scene'), 39)),
                            ('Age', -1), ('SpawnFrame', 0), ('Y', 0), ('Velocity', 0)]:
            setup.append(wg.node('data_addtolist', {'ITEM': value},
                                 {'LIST': wg.list('BOB.Number.' + name)}))
    setup.append(wg.call('SCENE | rebuild render queue'))
    _tail(wg, 'WORLD | create BOB objectives', setup)

    # The behavior spawns the number at the coin, 25 cm above its origin.
    count = mg.var('BOB.RedCoin.Count')
    coin_index = mg.var('BOB.RedCoin.CheckIndex', local=True)
    scene_index = mg.var('BOB.Number.PickupSceneIndex', local=True)
    replace = lambda name, value: mg.node('data_replaceitemoflist',
        {'INDEX': count, 'ITEM': value}, {'LIST': mg.list('BOB.Number.' + name)})
    write = lambda offset, value: _put(mg, mg.op('add', scene_index, offset), value)
    nodes = [mg.control('control_if', mg.op('lt', count, 8), [
        mg.set(scene_index, mg.item('BOB.Number.SceneIndex', count)),
        replace('Age', 0), replace('SpawnFrame', mg.var('SM64.SimulationFrame')),
        replace('Y', mg.op('add', mg.item('BOB.RedCoin.Y', coin_index), .25)),
        replace('Velocity', .26),
        write(10, mg.item('BOB.RedCoin.X', coin_index)),
        write(12, mg.item('BOB.Number.Y', count)),
        write(14, mg.item('BOB.RedCoin.Z', coin_index)),
        write(16, mg.var('cam rx')), write(18, mg.var('cam ry')), write(4, '3d')])]
    anchor = next(k for k, b in mg.blocks.items()
                  if b['opcode'] == 'event_broadcast'
                  and 'BOB RED COIN COLLECT' in str(b['inputs']))
    mg.insert_after(anchor, nodes)

    index = wg.var('BOB.Number.Index', local=True)
    idx = wg.var('BOB.Number.SceneIndex', local=True)
    age = wg.var('BOB.Number.Age', local=True)
    y = wg.var('BOB.Number.Y', local=True)
    velocity = wg.var('BOB.Number.Velocity', local=True)
    delta = wg.var('BOB.Number.TickDelta', local=True)
    put = lambda name, value: wg.node('data_replaceitemoflist',
        {'INDEX': index, 'ITEM': value}, {'LIST': wg.list('BOB.Number.' + name)})
    write = lambda offset, value: _put(wg, wg.op('add', idx, offset), value)
    # orange_number.inc.c: velocity 26, gravity -2, bounce 14 below -21,
    # deactivation at timer 35. One world unit equals 100 native cm.
    step = [wg.set(y, velocity, True), wg.set(velocity, -.02, True),
            wg.control('control_if', wg.op('lt', velocity, -.21000001), [wg.set(velocity, .14)]),
            wg.set(age, 1, True)]
    advance = [wg.set(delta, wg.op('subtract', wg.op('add', 1,
        wg.op('subtract', wg.var('SM64.SimulationFrame'),
              wg.item('BOB.Number.SpawnFrame', index))), age)),
        wg.control('control_if', wg.op('gt', delta, wg.op('subtract', 36, age)),
                   [wg.set(delta, wg.op('subtract', 36, age))]),
        wg.set(y, wg.item('BOB.Number.Y', index)),
        wg.set(velocity, wg.item('BOB.Number.Velocity', index)),
        wg.control('control_if', wg.op('gt', delta, 0), [
            wg.control('control_repeat', delta, step), put('Y', y), put('Velocity', velocity),
            put('Age', age), write(12, y)]),
        wg.control('control_if_else', wg.op('gt', age, 35),
                   [put('Age', -1), write(4, 'hidden')],
                   [write(16, wg.var('cam rx')), write(18, wg.var('cam ry'))])]
    wg.procedure('WORLD | sync red coin numbers', [wg.set(index, 1),
        wg.control('control_repeat', wg.length('BOB.Number.SceneIndex'), [
            wg.set(age, wg.item('BOB.Number.Age', index)),
            wg.control('control_if', wg.op('not', wg.op('lt', age, 0)), [
                wg.set(idx, wg.item('BOB.Number.SceneIndex', index)), *advance]),
            wg.set(index, 1, True)])])
    _tail(wg, 'WORLD | sync BOB objectives', [wg.call('WORLD | sync red coin numbers')])
    result = dict(version=1, number_models=7, triangles_per_number=2,
                  glyphs='ROM segment 2 RGBA16 digits; native 64 cm billboard',
                  counter='Native floating 1..7; eighth pickup reveals the red-coin star',
                  bounce='Native orange-number velocity/gravity/bounce; 36 simulation ticks',
                  audio='Committed count edge; ROM sound ID in M64 channel IO 4',
                  limitations='The final small sparkle on number expiry is not implemented')
    meta['red_coin_feedback_v22'] = result
    return result
