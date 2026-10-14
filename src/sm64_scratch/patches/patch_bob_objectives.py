"""Assetless BOB red coins, summit reward and usable cannons, revision 21.

The ROM builder supplies placement arrays, native meshes, animation and audio.
Cannon movement uses the existing four-substep collision solver at 30 Hz.
"""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition, _call

CREATE = 'SCENE | create object %s type %s model %s at %s %s %s rotation %s %s %s script %s'
EDIT = 'SCENE | edit object %s data %s value %s operation %s'
ACTION = 'SM64 STATE | set action %s arg %s'
ANIM = 'ANIM | request %s loop %s speed %s'
STAR = 'Bob-omb Power Star 1'
RED_STAR = 'BOB Eight Red Coins Star'


def _tail(g, code, nodes):
    key = _definition(g, code)
    while g.blocks[key].get('next'):
        key = g.blocks[key]['next']
    g.insert_after(key, nodes)


def _wrap(g, code, predicate, nodes):
    definition = _definition(g, code)
    old = g.blocks[definition]['next']
    guard = g.control('control_if_else', predicate, nodes)
    g.blocks[guard]['inputs']['SUBSTACK2'] = [2, old]
    g.blocks[old]['parent'] = guard
    g.blocks[guard]['parent'] = definition
    g.blocks[definition]['next'] = guard


def _broadcast(g, label):
    identity = 'bob_v21_' + label.lower().replace(' ', '_')
    g.stage['broadcasts'][identity] = label
    node = g.node('event_broadcast')
    g.blocks[node]['inputs'] = {'BROADCAST_INPUT': [1, [11, label, identity]]}
    return node


def _put(g, index, value):
    return g.node('data_replaceitemoflist', {'INDEX': index, 'ITEM': value}, {'LIST': g.list('Scene')})


def apply_bob_objectives(project):
    meta = project.setdefault('extensionData', {})
    world_present = next(t for t in project['targets'] if t['name'] == 'World + Assets')
    mario_present = next(t for t in project['targets'] if t['name'] == 'Mario')
    has = lambda target, code: any(b.get('mutation', {}).get('proccode') == code
                                  for b in target['blocks'].values())
    if (meta.get('bob_objectives_v21') and has(world_present, 'WORLD | create BOB objectives')
            and has(mario_present, 'BOB CANNON | hold and aim')):
        return meta['bob_objectives_v21']
    stage = next(t for t in project['targets'] if t.get('isStage'))
    world = next(t for t in project['targets'] if t['name'] == 'World + Assets')
    mario = next(t for t in project['targets'] if t['name'] == 'Mario')
    wg, mg = Graph(world, stage), Graph(mario, stage)
    for axis in 'XYZ':
        wg.list('BOB.RedCoin.' + axis)
        wg.list('BOB.Cannon.' + axis)
    wg.list('BOB.Cannon.Yaw')
    for name in ('BOB.RedCoin.Collected', 'BOB.RedCoin.SceneIndex', 'BOB.RedCoin.ModelName',
                 'BOB.Cannon.BaseIndex', 'BOB.Cannon.BarrelIndex'):
        wg.list(name)
    # Summit reward location used by the original King Bob-omb behavior.
    for name, value in zip(('Star.X', 'Star.Y', 'Star.Z'), (20, 45, -45)):
        var = wg.var(name)
        stage['variables'][var[2]][1] = value
        for block in wg.blocks.values():
            if block['opcode'] == 'data_setvariableto' and block.get('fields', {}).get('VARIABLE', [''])[0] == name:
                block['inputs']['VALUE'] = [1, [10, str(value)]]
    scene_name = wg.var('Star.SceneName', STAR)
    wg.var('Star.BOB4Collected')
    wg.var('BOB.RedCoin.Count')
    wg.var('BOB.RedCoin.AudioSkip')
    wg.var('BOB.Cannon.Active')
    wg.var('BOB.Cannon.Cooldown')
    wg.var('BOB.Cannon.Timer')
    wg.var('BOB.RedStar.SceneIndex')
    wg.var('BOB.RedStar.Visible')
    # The existing celebration follows whichever objective has been selected.
    defn = _definition(wg, 'WORLD | sync Power Star')
    todo, seen = [wg.blocks[defn]['next']], set()
    while todo:
        key = todo.pop()
        if not key or key in seen:
            continue
        seen.add(key)
        block = wg.blocks[key]
        for inp in block['inputs'].values():
            if isinstance(inp[1], str) and inp[1] in wg.blocks:
                todo.append(inp[1])
            elif isinstance(inp[1], list) and inp[1] == [10, STAR]:
                inp[:] = [3, [12, scene_name[1], scene_name[2]], [10, '']]
        todo.append(block.get('next'))

    _reticle(project)
    _world(wg)
    _coins_and_stars(mg)
    _cannon(mg)
    _tail(wg, 'WORLD | build Bob-omb Battlefield scene', [wg.call('WORLD | create BOB objectives')])
    _tail(wg, 'WORLD | sync visuals', [wg.control('control_if', wg.op('equals', wg.var('Level.ID'), 'BOB'),
                                                        [wg.call('WORLD | sync BOB objectives')])])
    mg.insert_after('Wg', [mg.call('BOB CANNON | check entry')])
    mg.insert_after('+=', [mg.call('BOB COINS | check red coin collection')])
    resets = lambda g: [g.set(g.var(name), 0) for name in ('Star.BOB4Collected', 'BOB.RedCoin.Count',
                  'BOB.RedCoin.AudioSkip', 'BOB.Cannon.Active', 'BOB.Cannon.Cooldown', 'BOB.Cannon.WasHidden')]
    wg.hat('event_whenflagclicked', resets(wg))
    _tail(wg, 'WORLD | initialize', resets(wg))
    meta['bob_objectives_v21'] = dict(version=1, red_coins=8, cannons=6,
        summit_reward='King Bob-omb reward position', cannon_controls='W/S pitch, A/D yaw, Space fire',
        placements='ROM BOB macro table', cannon_unlock='All six immediately usable; buddy dialogue is not implemented',
        physics='Native launch speed 100, launch offset 120 cm, gravity -1 per 30 Hz tick, air collision substeps',
        limitations='Summit star substitutes for the unimplemented King Bob-omb fight')
    return meta['bob_objectives_v21']


def _world(g):
    v = g.var
    i = v('BOB.Objective.Index', local=True)
    idx = v('BOB.Objective.SceneIndex', local=True)
    frame = v('BOB.RedCoin.Frame', local=True)
    old_frame = v('BOB.RedCoin.PreviousFrame', -1, local=True)
    join = lambda a, b: g.node('operator_join', {'STRING1': a, 'STRING2': b})
    add = lambda name, val: g.node('data_addtolist', {'ITEM': val}, {'LIST': g.list(name)})
    clear = lambda name: g.node('data_deletealloflist', fields={'LIST': g.list(name)})
    create = lambda name, model, x, y, z, rx=0, ry=0, type_='3d': _call(g, CREATE, [name, type_, model, x, y, z, rx, ry, 0, ''])
    edit = lambda name, field, value: _call(g, EDIT, [name, field, value, 'set'])
    setup = [g.set(v('BOB.RedCoin.Count'), 0), g.set(v('BOB.RedCoin.AudioSkip'), 0),
             g.set(v('BOB.Cannon.Active'), 0), g.set(v('BOB.Cannon.Cooldown'), 0),
             g.set(v('BOB.Cannon.WasHidden'), 0), g.set(v('Star.SceneName', STAR), STAR),
             g.set(v('BOB.RedStar.Visible'), 0), g.set(old_frame, -1)]
    for name in ('BOB.RedCoin.Collected', 'BOB.RedCoin.SceneIndex', 'BOB.RedCoin.ModelName',
                 'BOB.Cannon.BaseIndex', 'BOB.Cannon.BarrelIndex'):
        setup.append(clear(name))
    for n in range(1, 5):
        name = f'Red Coin Frame {n}'
        setup.extend([_call(g, 'ASSET | load model %s', [name]), add('BOB.RedCoin.ModelName', name)])
    setup.extend([g.set(i, 1), g.control('control_repeat', g.length('BOB.RedCoin.X'), [
        create(join('BOB Red Coin ', i), 'Red Coin Frame 1', g.item('BOB.RedCoin.X', i),
               g.item('BOB.RedCoin.Y', i), g.item('BOB.RedCoin.Z', i)),
        add('BOB.RedCoin.SceneIndex', g.op('subtract', g.length('Scene'), 39)),
        add('BOB.RedCoin.Collected', 0), g.set(i, 1, True)]),
        create(RED_STAR, 'Power Star', -60, 10, 24, type_='hidden'),
        g.set(v('BOB.RedStar.SceneIndex'), g.op('subtract', g.length('Scene'), 39)),
        g.control('control_if', g.op('equals', v('Star.BOB4Collected'), 1), [edit(RED_STAR, 'model', 'Power Star Collected')]),
        edit('BOB Water Bomb Cannon Base', 'type', 'hidden'), edit('BOB Water Bomb Cannon Barrel', 'type', 'hidden')])
    for n in range(2, 7):
        setup.append(edit(f'BOB Cannon Lid {n}', 'type', 'hidden'))
    setup.extend([g.set(i, 1), g.control('control_repeat', g.length('BOB.Cannon.X'), [
        create(join('BOB Usable Cannon Base ', i), 'BOB Cannon Base', g.item('BOB.Cannon.X', i),
               g.item('BOB.Cannon.Y', i), g.item('BOB.Cannon.Z', i), ry=g.item('BOB.Cannon.Yaw', i)),
        add('BOB.Cannon.BaseIndex', g.op('subtract', g.length('Scene'), 39)),
        create(join('BOB Usable Cannon Barrel ', i), 'BOB Cannon Barrel', g.item('BOB.Cannon.X', i),
               g.item('BOB.Cannon.Y', i), g.item('BOB.Cannon.Z', i), rx=0, ry=g.item('BOB.Cannon.Yaw', i)),
        add('BOB.Cannon.BarrelIndex', g.op('subtract', g.length('Scene'), 39)), g.set(i, 1, True)]),
        g.call('SCENE | rebuild render queue')])
    g.procedure('WORLD | create BOB objectives', setup)
    write = lambda off, value: _put(g, g.op('add', idx, off), value)
    coin_sync = [g.set(idx, g.item('BOB.RedCoin.SceneIndex', i)),
        g.control('control_if_else', g.op('equals', g.item('BOB.RedCoin.Collected', i), 1),
            [write(4, 'hidden')], [write(18, v('cam ry')), g.control('control_if',
                g.op('not', g.op('equals', frame, old_frame)), [write(6, g.item('BOB.RedCoin.ModelName', frame))])]),
        g.set(i, 1, True)]
    cannon_sync = []
    for part, listname in (('Base', 'BOB.Cannon.BaseIndex'), ('Barrel', 'BOB.Cannon.BarrelIndex')):
        cannon_sync.extend([g.set(idx, g.item(listname, v('BOB.Cannon.Index'))),
            write(12, g.op('add', v('BOB.Cannon.CenterY'), v('BOB.Cannon.Rise'))),
            write(18, v('BOB.Cannon.AimYaw'))])
        if part == 'Barrel':
            cannon_sync.append(write(16, v('BOB.Cannon.Pitch')))
        # Both pieces disappear once the native first-person cannon mode starts.
        cannon_sync.append(g.control('control_if_else', g.op('gt', v('BOB.Cannon.Timer'), 122),
                                                        [write(4, 'hidden')], [write(4, '3d')]))
    cannon_sync.extend([edit('Mario', 'type', 'hidden'), edit('Mario Shadow', 'type', 'hidden'),
                        g.set(v('BOB.Cannon.WasHidden'), 1)])
    restore = [edit('Mario', 'type', '3d'), edit('Mario Shadow', 'type', '3d'),
               g.set(idx, g.item('BOB.Cannon.BarrelIndex', v('BOB.Cannon.Index'))), write(4, '3d'),
               write(12, v('BOB.Cannon.CenterY')), write(16, 0),
               g.set(idx, g.item('BOB.Cannon.BaseIndex', v('BOB.Cannon.Index'))), write(12, v('BOB.Cannon.CenterY')),
               g.set(v('BOB.Cannon.WasHidden'), 0)]
    sync = [g.set(frame, g.op('add', g.op('mod', g.math('floor', g.op('divide', v('SM64.SimulationFrame'), 2)), 4), 1)),
            g.set(i, 1), g.control('control_repeat', g.length('BOB.RedCoin.SceneIndex'), coin_sync), g.set(old_frame, frame),
            g.control('control_if', g.op('and', g.op('equals', v('BOB.RedCoin.Count'), 8),
                g.op('equals', v('BOB.RedStar.Visible'), 0)), [
                edit(RED_STAR, 'type', '3d'), g.set(v('BOB.RedStar.Visible'), 1),
                _broadcast(g, 'BOB RED STAR APPEAR')]),
            g.control('control_if', g.op('and', g.op('equals', v('BOB.RedStar.Visible'), 1),
                g.op('not', g.op('equals', v('Star.SceneName'), RED_STAR))), [
                g.set(idx, v('BOB.RedStar.SceneIndex')), write(18, v('Star.Yaw'))]),
            g.control('control_if_else', g.op('equals', v('BOB.Cannon.Active'), 1), cannon_sync, [
                g.control('control_if', g.op('equals', v('BOB.Cannon.WasHidden'), 1), restore)])]
    # The native cannon is visible only within 500 cm. Avoid rendering five
    # distant barrel/base meshes on every frame of normal gameplay.
    visible = v('BOB.Cannon.VisibleType', local=True)
    idle = [g.set(visible, 'hidden'), g.control('control_if', g.op('and',
        g.op('lt', g.math('abs', g.op('subtract', v('SM64.PosX'), g.item('BOB.Cannon.X', i))), 5),
        g.op('and', g.op('lt', g.math('abs', g.op('subtract', v('SM64.PosZ'), g.item('BOB.Cannon.Z', i))), 5),
                   g.op('lt', g.math('abs', g.op('subtract', v('SM64.PosY'), g.item('BOB.Cannon.Y', i))), 5))),
        [g.set(visible, '3d')]),
        g.set(idx, g.item('BOB.Cannon.BaseIndex', i)), write(4, visible),
        g.set(idx, g.item('BOB.Cannon.BarrelIndex', i)), write(4, visible), g.set(i, 1, True)]
    sync.append(g.control('control_if', g.op('equals', v('BOB.Cannon.Active'), 0), [
        g.set(i, 1), g.control('control_repeat', g.length('BOB.Cannon.BaseIndex'), idle)]))
    g.procedure('WORLD | sync BOB objectives', sync)


def _coins_and_stars(g):
    v = g.var
    i = v('BOB.RedCoin.CheckIndex', local=True)
    d = lambda axis: g.op('subtract', v('SM64.Pos' + axis), g.item('BOB.RedCoin.' + axis, i))
    valid = lambda: g.op('and', g.op('equals', v('Level.ID'), 'BOB'), g.op('and',
        g.op('not', g.op('equals', v('SM64.ActionGroup'), 'CUTSCENE')), g.op('equals', v('BOB.Cannon.Active'), 0)))
    close = g.op('and', g.op('equals', g.item('BOB.RedCoin.Collected', i), 0),
        g.op('and', g.op('lt', g.op('add', g.op('multiply', d('X'), d('X')), g.op('multiply', d('Z'), d('Z'))), 1.8769),
             g.op('lt', g.math('abs', g.op('subtract', d('Y'), -.65)), 1.25)))
    collect = [g.node('data_replaceitemoflist', {'INDEX': i, 'ITEM': 1}, {'LIST': g.list('BOB.RedCoin.Collected')}),
        g.set(v('BOB.RedCoin.Count'), 1, True), g.set(v('Coin.Count'), 2, True),
        g.set(v('SM64.HealCounter'), 8, True), g.set(v('BOB.RedCoin.AudioSkip'), 2, True),
        _broadcast(g, 'BOB RED COIN COLLECT')]
    # Pickups above moving platforms and tree tops use the actual player height.
    g.procedure('BOB COINS | check red coin collection', [g.control('control_if', valid(), [
        g.set(i, 1), g.control('control_repeat', g.length('BOB.RedCoin.X'), [
            g.control('control_if', close, collect), g.set(i, 1, True)])])])
    # Reuse the existing dance/return implementation, with a separate ownership flag.
    root = 'ultimate_b_35500044'
    body = g.blocks[root]['inputs']['SUBSTACK'][1]
    # Replace the old ownership check with a selected objective check.
    ownership = g.control('control_if_else', g.op('equals', v('BOB.Star.Kind'), 4), [
        g.control('control_if', g.op('equals', v('Star.BOB4Collected'), 0), [
            g.set(v('Star.BOB4Collected'), 1), g.set(v('Star.Count'), 1, True)])], [
        g.control('control_if', g.op('equals', v('Star.BOB1Collected'), 0), [
            g.set(v('Star.BOB1Collected'), 1), g.set(v('Star.Count'), 1, True)])])
    following = g.blocks[body]['next']
    g.blocks[ownership]['next'] = following
    g.blocks[following]['parent'] = ownership
    definition = g.node('procedures_definition')
    prototype = g.node('procedures_prototype')
    g.blocks[definition].update(topLevel=True, x=1400, y=0, inputs={'custom_block': [1, prototype]}, next=ownership)
    g.blocks[ownership]['parent'] = definition
    g.blocks[prototype].update(parent=definition, shadow=True,
        mutation={'tagName': 'mutation', 'children': [], 'proccode': 'BOB STAR | collect selected objective',
                  'argumentids': '[]', 'argumentnames': '[]', 'argumentdefaults': '[]', 'warp': 'true'})
    def near(x, y, z):
        return g.op('and', g.op('equals', v('Star.State'), 0), g.op('and',
            g.op('lt', g.math('abs', g.op('subtract', v('SM64.PosX'), x)), 1.1),
            g.op('and', g.op('lt', g.math('abs', g.op('subtract', v('SM64.PosY'), y)), 2.2),
                       g.op('lt', g.math('abs', g.op('subtract', v('SM64.PosZ'), z)), 1.1))))
    replacement = [g.control('control_if', valid(), [
        g.control('control_if', near(v('Star.X'), v('Star.Y'), v('Star.Z')), [
            g.set(v('BOB.Star.Kind'), 1), g.set(v('Star.SceneName'), STAR), g.call('BOB STAR | collect selected objective')]),
        g.control('control_if', g.op('and', g.op('equals', v('BOB.RedCoin.Count'), 8), near(-60, 10, 24)), [
            g.set(v('BOB.Star.Kind'), 4), g.set(v('Star.SceneName'), RED_STAR), g.call('BOB STAR | collect selected objective')])])]
    defn = _definition(g, 'SM64 STAR | check collection')
    g.blocks[defn]['next'] = g.chain(replacement, defn)
    # Detached former check must not keep a second parent reference to live dance nodes.
    g.blocks[root]['inputs']['SUBSTACK'] = [2, None]
    g.blocks[body]['next'] = None
    def remove(key):
        if not key or key not in g.blocks:
            return
        block = g.blocks.pop(key)
        for inp in block.get('inputs', {}).values():
            for child in inp[1:]:
                if isinstance(child, str) and child in g.blocks:
                    remove(child)
        remove(block.get('next'))
    remove(root)
    remove(body)


def _cannon(g):
    v = g.var
    i = v('BOB.Cannon.CheckIndex', local=True)
    eq = lambda name, value: g.op('equals', v(name), value)
    action = lambda name: _call(g, ACTION, [name, 0])
    anim = lambda name: _call(g, ANIM, [name, 1, 1])
    atan = lambda y, x: _call(g, 'SM64 MATH | atan2 y %s x %s', [y, x])
    # A bounded course-only search; a cooldown prevents immediate recapture.
    near = lambda axis: g.math('abs', g.op('subtract', v('SM64.Pos' + axis), g.item('BOB.Cannon.' + axis, i)))
    entry = [g.set(v('BOB.Cannon.Active'), 1), g.set(v('BOB.Cannon.Index'), i),
             g.set(v('BOB.Cannon.Timer'), 0), g.set(v('BOB.Cannon.Rise'), 0),
             g.set(v('BOB.Cannon.Pitch'), 0), g.set(v('BOB.Cannon.OffsetYaw'), 0),
             g.set(v('BOB.Cannon.BaseYaw'), g.item('BOB.Cannon.Yaw', i)),
             g.set(v('BOB.Cannon.AimYaw'), g.item('BOB.Cannon.Yaw', i))]
    for axis in 'XYZ':
        entry.append(g.set(v('BOB.Cannon.Center' + axis), g.item('BOB.Cannon.' + axis, i)))
        entry.append(g.set(v('SM64.Vel' + axis), 0))
    entry.extend([atan(g.op('subtract', v('cam x'), v('BOB.Cannon.CenterX')),
                       g.op('subtract', v('cam z'), v('BOB.Cannon.CenterZ'))),
                  g.set(v('BOB.Cannon.CameraYaw'), v('Math.Result')),
                  g.set(v('BOB.Cannon.CameraDistance'), g.math('sqrt', g.op('add',
                       g.op('multiply', g.op('subtract', v('cam x'), v('BOB.Cannon.CenterX')), g.op('subtract', v('cam x'), v('BOB.Cannon.CenterX'))),
                       g.op('multiply', g.op('subtract', v('cam z'), v('BOB.Cannon.CenterZ')), g.op('subtract', v('cam z'), v('BOB.Cannon.CenterZ')))))),
                  g.set(v('SM64.ForwardVel'), 0), action('ACT_IN_CANNON'), _broadcast(g, 'BOB CANNON ENTER')])
    valid = g.op('and', eq('Level.ID', 'BOB'), g.op('and', eq('BOB.Cannon.Active', 0),
        g.op('and', eq('BOB.Cannon.Cooldown', 0), g.op('and', eq('Star.State', 0),
            g.op('and', g.op('not', eq('SM64.ActionGroup', 'CUTSCENE')), g.op('lt', v('SM64.VelY'), 1))))))
    search = [g.set(i, 1), g.control('control_repeat', g.length('BOB.Cannon.X'), [
        g.control('control_if', g.op('and', eq('BOB.Cannon.Active', 0), g.op('and',
            g.op('lt', near('X'), .9), g.op('and', g.op('lt', near('Z'), .9), g.op('lt', near('Y'), 2)))), entry),
        g.set(i, 1, True)])]
    g.procedure('BOB CANNON | check entry', [
        g.control('control_if', g.op('and', eq('BOB.Cannon.Active', 1),
            g.op('not', eq('SM64.Action', 'ACT_IN_CANNON'))), [g.set(v('BOB.Cannon.Active'), 0),
                g.set(v('BOB.Cannon.Cooldown'), 90)]),
        g.control('control_if', g.op('gt', v('BOB.Cannon.Cooldown'), 0), [g.set(v('BOB.Cannon.Cooldown'), -1, True)]),
        g.control('control_if', valid, search)])
    aim = [g.set(v('BOB.Cannon.Pitch'), g.op('multiply', v('SM64.StickY'), .054931640625), True),
           g.set(v('BOB.Cannon.OffsetYaw'), g.op('multiply', v('SM64.StickX'), -.054931640625), True)]
    for name, lo, hi in (('BOB.Cannon.Pitch', 0, 79.997), ('BOB.Cannon.OffsetYaw', -90, 90)):
        aim.extend([g.control('control_if', g.op('lt', v(name), lo), [g.set(v(name), lo)]),
                    g.control('control_if', g.op('gt', v(name), hi), [g.set(v(name), hi)])])
    aim.extend([g.set(v('BOB.Cannon.AimYaw'), g.op('mod', g.op('add', v('BOB.Cannon.BaseYaw'), v('BOB.Cannon.OffsetYaw')), 360)),
        g.control('control_if', g.op('and', g.op('gt', v('SM64.StickMag'), 0),
            g.op('equals', g.op('mod', v('SM64.SimulationFrame'), 6), 0)), [_broadcast(g, 'BOB CANNON AIM')])])
    direction = lambda axis: g.op('multiply', g.math('cos', v('BOB.Cannon.Pitch')),
                                g.math('sin' if axis == 'X' else 'cos', v('BOB.Cannon.AimYaw')))
    shot = [g.set(v('SM64.FaceYaw'), v('BOB.Cannon.AimYaw')),
        g.set(v('SM64.VelY'), g.op('multiply', 100, g.math('sin', v('BOB.Cannon.Pitch')))),
        g.set(v('SM64.ForwardVel'), g.op('multiply', 100, g.math('cos', v('BOB.Cannon.Pitch')))),
        g.set(v('SM64.PosX'), g.op('multiply', 1.2, direction('X')), True),
        g.set(v('SM64.PosY'), g.op('multiply', 1.2, g.math('sin', v('BOB.Cannon.Pitch'))), True),
        g.set(v('SM64.PosZ'), g.op('multiply', 1.2, direction('Z')), True),
        g.set(v('BOB.Cannon.Active'), 0), g.set(v('BOB.Cannon.Cooldown'), 90),
        action('ACT_SHOT_FROM_CANNON'),
        _call(g, 'SM64 MOVE | set forward velocity %s', [v('SM64.ForwardVel')]),
        g.set(v('CamSM64.Init'), 0), g.set(v('Camera64.PendingSnap'), 0),
        _broadcast(g, 'BOB CANNON FIRE')]
    aim.append(g.control('control_if', eq('SM64.InputAPressed', 1), shot))
    hold = [g.set(v('BOB.Cannon.Timer'), 1, True),
        g.control('control_if', eq('BOB.Cannon.Timer', 69), [_broadcast(g, 'BOB CANNON ROTATE')]),
        g.control('control_if', eq('BOB.Cannon.Timer', 97), [_broadcast(g, 'BOB CANNON TILT')]),
        g.control('control_if', g.op('and', g.op('gt', v('BOB.Cannon.Timer'), 74), g.op('lt', v('BOB.Cannon.Timer'), 91)), [
            g.set(v('BOB.Cannon.AimYaw'), g.op('add', v('BOB.Cannon.BaseYaw'),
                g.op('multiply', 90, g.math('sin', g.op('multiply', g.op('subtract', v('BOB.Cannon.Timer'), 75), 5.625)))))]),
        g.control('control_if', g.op('and', g.op('gt', v('BOB.Cannon.Timer'), 100), g.op('lt', v('BOB.Cannon.Timer'), 117)), [
            g.set(v('BOB.Cannon.Pitch'), g.op('multiply', 45, g.math('sin',
                g.op('multiply', g.op('subtract', v('BOB.Cannon.Timer'), 100), 5.625))))]),
        g.control('control_if', eq('BOB.Cannon.Timer', 123), [g.set(v('BOB.Cannon.AimYaw'), v('BOB.Cannon.BaseYaw')),
            g.set(v('BOB.Cannon.Pitch'), 45)]),
        g.control('control_if', g.op('lt', v('BOB.Cannon.Timer'), 69), [g.set(v('BOB.Cannon.Rise'), .05, True)]),
        g.set(v('SM64.PosX'), v('BOB.Cannon.CenterX')), g.set(v('SM64.PosY'), g.op('add', v('BOB.Cannon.CenterY'), 3.5)),
        g.set(v('SM64.PosZ'), v('BOB.Cannon.CenterZ')), anim('Belly flop'),
        g.control('control_if', g.op('gt', v('BOB.Cannon.Timer'), 122), aim)]
    g.procedure('BOB CANNON | hold and aim', hold)
    _wrap(g, 'SM64 ACTION | automatic', eq('SM64.Action', 'ACT_IN_CANNON'), [g.call('BOB CANNON | hold and aim')])
    flight = [anim('SM64 Cannon Flight'), _call(g, 'SM64 MOVE | set forward velocity %s', [v('SM64.ForwardVel')]),
        g.call('SM64 STEP | perform air step'), atan(v('SM64.ForwardVel'), v('SM64.VelY')),
        g.set(v('SM64.GfxPitch'), g.op('subtract', v('Math.Result'), 90)),
        g.control('control_if_else', eq('SM64.Landed', 1), [action('ACT_DIVE_SLIDE')], [
            g.control('control_if', eq('SM64.StepResult', 'AIR_STEP_HIT_WALL'), [
                _call(g, 'SM64 MOVE | set forward velocity %s', [-16]),
                g.control('control_if', g.op('gt', v('SM64.VelY'), 0), [g.set(v('SM64.VelY'), 0)]),
                g.call('SM64 FX | spawn vertical bonk stars'), action('ACT_BACKWARD_AIR_KB')])]),
        g.control('control_if', g.op('gt', v('SM64.ForwardVel'), 0), [g.set(v('SM64.ForwardVel'), -.05, True)])]
    _wrap(g, 'SM64 ACTION | airborne', eq('SM64.Action', 'ACT_SHOT_FROM_CANNON'), flight)
    _wrap(g, 'SM64 MOVE | apply gravity', eq('SM64.Action', 'ACT_SHOT_FROM_CANNON'), [
        g.set(v('SM64.VelY'), -1, True), g.control('control_if', g.op('lt', v('SM64.VelY'), -75), [g.set(v('SM64.VelY'), -75)])])
    cam = [g.set(v('cam x'), v('BOB.Cannon.CenterX')), g.set(v('cam y'), g.op('add', v('BOB.Cannon.CenterY'),
                g.op('add', v('BOB.Cannon.Rise'), .8))), g.set(v('cam z'), v('BOB.Cannon.CenterZ')),
           g.set(v('cam ry'), v('BOB.Cannon.AimYaw')), g.set(v('cam rx'), v('BOB.Cannon.Pitch')),
           g.set(v('cam rz'), 0), g.set(v('Camera64.Yaw'), g.op('mod', g.op('add', v('BOB.Cannon.AimYaw'), 180), 360))]
    # Seed the normal camera from the last flight frame at release/landing.
    for axis in 'XYZ':
        cam.append(g.set(v('Camera64.Goal' + axis), v('cam ' + axis.lower())))
        cam.append(g.set(v('Camera64.Focus' + axis), g.op('add', v('cam ' + axis.lower()),
            g.op('multiply', 10, g.math('sin', v('BOB.Cannon.Pitch'))) if axis == 'Y' else
            g.op('multiply', 10, direction(axis)))))
    orbit_yaw = lambda: g.op('add', v('BOB.Cannon.CameraYaw'), g.op('multiply', v('BOB.Cannon.Timer'), .3515625))
    radius = lambda: g.op('add', v('BOB.Cannon.CameraDistance'),
        g.op('multiply', g.op('subtract', 4, v('BOB.Cannon.CameraDistance')),
             g.op('divide', v('BOB.Cannon.Timer'), 123)))
    raise_cam = [
        g.set(v('cam x'), g.op('add', v('BOB.Cannon.CenterX'), g.op('multiply', radius(), g.math('sin', orbit_yaw())))),
        g.set(v('cam z'), g.op('add', v('BOB.Cannon.CenterZ'), g.op('multiply', radius(), g.math('cos', orbit_yaw())))),
        g.set(v('cam y'), g.op('add', v('BOB.Cannon.CenterY'), g.op('add', v('BOB.Cannon.Rise'), 2))),
        g.set(v('Camera64.FocusX'), v('BOB.Cannon.CenterX')),
        g.set(v('Camera64.FocusY'), g.op('add', v('BOB.Cannon.CenterY'), g.op('add', v('BOB.Cannon.Rise'), 1))),
        g.set(v('Camera64.FocusZ'), v('BOB.Cannon.CenterZ')),
        atan(g.op('subtract', v('Camera64.FocusX'), v('cam x')), g.op('subtract', v('Camera64.FocusZ'), v('cam z'))),
        g.set(v('cam ry'), v('Math.Result')),
        g.set(v('cam rx'), g.op('multiply', -1, g.math('atan', g.op('divide', 1, radius())))),
        g.set(v('cam rz'), 0)]
    _wrap(g, 'CAMERA | update isolated', eq('BOB.Cannon.Active', 1), [
        g.control('control_if_else', g.op('lt', v('BOB.Cannon.Timer'), 123), raise_cam, cam)])


def build_cannon_reticle_assets(rom):
    """Read the native triangle; reproduce render_hud_cannon_reticle transforms."""
    import math
    import struct
    from sm64_scratch.rom_codec import mio0
    segment = mio0(rom, 1083968)
    # 0x0200EF30 is the source label; US segment 2 places this Vtx at
    # 0x11D90. Reading the source label interpreted unrelated data as huge
    # triangles that obscured the entire first-person view.
    vertices = [struct.unpack_from('>3h', segment, 0x11D90 + 16 * i)[:2] for i in range(3)]
    if vertices != [(0, 0), (8, 8), (0, 16)]:
        raise ValueError('Unsupported cannon reticle Vtx layout in US segment 2')
    paths = []
    for dx, dy, degrees in [(-20, -8, 0), (20, 8, 180), (8, -20, 90), (-8, 20, -90)]:
        angle = math.radians(degrees)
        points = [(36 + 1.5 * (dx + x * math.cos(angle) - y * math.sin(angle)),
                   36 - 1.5 * (dy + x * math.sin(angle) + y * math.cos(angle))) for x, y in vertices]
        paths.append('<polygon points="' + ' '.join(f'{x:.4f},{y:.4f}' for x, y in points) + '"/>')
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="72" height="72">'
           '<g fill="rgb(50,50,50)" opacity="0.705882">' + ''.join(paths) + '</g></svg>').encode()
    return {'BOB Cannon Aim': [('Reticle', svg)]}


def _reticle(project):
    from sm64_scratch.patches.patch_game_features import _sprite
    sprite, stage, g = _sprite(project, 'BOB Cannon Aim')
    sprite['layerOrder'] = 115
    sprite['costumes'][0].update(name='Reticle', rotationCenterX=16, rotationCenterY=16)
    valid = g.op('and', g.op('equals', g.var('TitleIntro.Active'), 0),
        g.op('and', g.op('equals', g.var('BOB.Cannon.Active'), 1),
                   g.op('gt', g.var('BOB.Cannon.Timer'), 122)))
    g.hat('event_whenbroadcastreceived', [g.control('control_if_else', valid,
        [g.node('looks_show')], [g.node('looks_hide')])], ['ENGINE FRAME RENDERED', 'porter_frame_rendered'])
    g.hat('event_whenflagclicked', [g.node('looks_hide')])
    g.hat('event_whenbroadcastreceived', [g.node('looks_hide')], ['TITLE INTRO RUN', 'titleintro_bc_run'])
