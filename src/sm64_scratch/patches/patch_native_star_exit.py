"""Native-style Power Star exit camera/wipes and standard coin shadows.

Assetless patch: the Mario-head transition image is decoded later from the
user's ROM by decode_transitions.py.  This module only adds Scratch logic and
transparent costume placeholders.
"""
from __future__ import annotations

from copy import deepcopy

from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition, _call
from sm64_scratch.patches.patch_bob_objectives import _wrap, _tail

ANIM = 'ANIM | request %s loop %s speed %s'
SET_ACTION = 'SM64 STATE | set action %s arg %s'
DRAW_OBJECT = ('RENDER | draw object model %s position %s %s %s rotation %s %s %s '
               'scale %s %s %s bone model %s')


def _arg(g, name):
    return g.node('argument_reporter_string_number', fields={'VALUE': [name, None]})


def _contains(g, haystack, needle):
    return g.node('operator_contains', {'STRING1': haystack, 'STRING2': needle})


def _set_cutscene_action(g, action):
    v = g.var
    return [
        g.set(v('SM64.PrevAction'), v('SM64.Action')),
        g.set(v('SM64.Action'), action),
        g.set(v('SM64.ActionGroup'), 'CUTSCENE'),
        g.set(v('SM64.ActionArg'), 0),
        g.set(v('SM64.ActionState'), 0),
        g.set(v('SM64.ActionTimer'), 0),
        g.set(v('SM64.ActionChanged'), 1),
    ]


def _ensure_transition_placeholders(project):
    target = next(t for t in project['targets'] if t['name'] == 'Transitions')
    names = [c['name'] for c in target['costumes']]
    if 'Mario exit 00' in names:
        return
    template = deepcopy(target['costumes'][0])
    # decode_transitions.py replaces these transparent placeholders locally.
    for name in [f'Mario exit {i:02d}' for i in range(32)] + [f'Star return {i:02d}' for i in range(17)]:
        target['costumes'].append(dict(template, name=name, bitmapResolution=1,
                                      rotationCenterX=240, rotationCenterY=180))


def _transition_logic(project):
    stage = next(t for t in project['targets'] if t.get('isStage'))
    target = next(t for t in project['targets'] if t['name'] == 'Transitions')
    g = Graph(target, stage)
    if any(b.get('mutation', {}).get('proccode') == 'WIPE | present native star exit'
           for b in g.blocks.values()):
        return
    v = g.var
    active = v('Star.ExitTransition')
    start = v('Star.ExitTransitionStart')
    frame = v('Star.ExitTransitionFrame')
    ret = v('Star.ReturnTransition')
    rstart = v('Star.ReturnTransitionStart')
    rframe = v('Star.ReturnTransitionFrame')
    elapsed = v('Star.TransitionElapsed', local=True)

    def common():
        return [g.node('motion_gotoxy', {'X': 0, 'Y': 0}),
                g.node('looks_setsizeto', {'SIZE': 100}),
                g.node('looks_gotofrontback', fields={'FRONT_BACK': ['front', None]}),
                g.node('looks_cleargraphiceffects')]
    # Existing costumes 1..18 are painting-entry assets. New Mario close is 19..50.
    close = [*common(),
             g.set(elapsed, g.op('subtract', g.node('sensing_timer'), start)),
             g.set(frame, g.math('floor', g.op('multiply', elapsed, 30))),
             g.control('control_if', g.op('lt', frame, 0), [g.set(frame, 0)]),
             g.control('control_if', g.op('gt', frame, 31), [g.set(frame, 31)]),
             g.node('looks_switchcostumeto', {'COSTUME': g.op('add', frame, 19)}),
             g.node('looks_show')]
    # Return reveal follows native default spawn transition: black star, 16 ticks.
    reveal = [*common(),
              g.set(elapsed, g.op('subtract', g.node('sensing_timer'), rstart)),
              g.set(rframe, g.math('floor', g.op('multiply', elapsed, 30))),
              g.control('control_if', g.op('lt', rframe, 0), [g.set(rframe, 0)]),
              g.control('control_if_else', g.op('lt', rframe, 16), [
                  g.node('looks_switchcostumeto', {'COSTUME': g.op('add', rframe, 51)}),
                  g.node('looks_show')], [g.node('looks_hide'), g.set(ret, 0)])]
    g.procedure('WIPE | present native star exit', [
        g.control('control_if_else', g.op('equals', active, 1), close, [
            g.control('control_if', g.op('equals', ret, 1), reveal)])])

    # Existing render-complete hat presents painting wipe first; star exit then owns the same sprite.
    hat = next(k for k,b in g.blocks.items() if b['opcode'] == 'event_whenbroadcastreceived'
               and b.get('fields', {}).get('BROADCAST_OPTION', [''])[0] == 'ENGINE FRAME RENDERED')
    tail = hat
    while g.blocks[tail].get('next'):
        tail = g.blocks[tail]['next']
    g.insert_after(tail, [g.call('WIPE | present native star exit')])
    # Reset only our state; do not interfere with painting wipe state.
    flag = next(k for k,b in g.blocks.items() if b['opcode'] == 'event_whenflagclicked')
    g.insert_after(flag, [g.set(active, 0), g.set(ret, 0), g.set(frame, 0), g.set(rframe, 0)])


def _reachable_statements(g, code):
    """Visit only the live procedure body, excluding old detached revisions."""
    pending = [g.blocks[_definition(g, code)].get('next')]
    seen = set()
    while pending:
        key = pending.pop()
        if not key or key in seen:
            continue
        seen.add(key)
        block = g.blocks[key]
        yield key, block
        pending.append(block.get('next'))
        pending.extend(value[1] for name, value in block.get('inputs', {}).items()
                       if name.startswith('SUBSTACK'))


def _prepare_star_collection(g):
    """Give every pickup its own objective kind and fresh exit/camera state."""
    for code in ('SM64 CHOMP | check gate star', 'BOB STAR | collect selected objective'):
        anchors = [key for key, block in _reachable_statements(g, code)
                   if block['opcode'] == 'data_setvariableto'
                   and block.get('fields', {}).get('VARIABLE', [''])[0] == 'Star.State']
        if len(anchors) != 1:
            raise ValueError('Expected one live star collection commit in ' + code)
        body = []
        if code == 'SM64 CHOMP | check gate star':
            # The legacy Chomp collector bypasses the selected-objective helper.
            # In particular, it must not inherit kind 7 from a 100-coin pickup.
            body += [g.set(g.var('BOB.Star.Kind'), 6),
                     g.set(g.var('Star.SceneName'), 'Bob-omb Chain Chomp Star')]
        body += [g.set(g.var('Star.NativeCamLastFrame'), -1),
                 g.set(g.var('Star.ExitTransition'), 0),
                 g.set(g.var('Star.ReturnTransition'), 0),
                 g.set(g.var('Star.NativeReturnPending'), 0)]
        g.insert_after(anchors[0], body)


def _mario_star_logic(project):
    stage = next(t for t in project['targets'] if t.get('isStage'))
    mario = next(t for t in project['targets'] if t['name'] == 'Mario')
    g = Graph(mario, stage)
    if any(b.get('mutation', {}).get('proccode') == 'SM64 STAR | native exit dance'
           for b in g.blocks.values()):
        return
    v = g.var
    eq = lambda name, value: g.op('equals', v(name), value)

    _prepare_star_collection(g)

    # ---- Star-dance camera ----
    dist = v('Star.NativeCamDistance')
    pitch = v('Star.NativeCamPitch')
    yaw = v('Star.NativeCamYaw')
    last = v('Star.NativeCamLastFrame', -1)
    t = v('SM64.ActionTimer')
    init = [g.set(dist, v('Camera64.Distance')), g.set(pitch, v('Camera64.Pitch')),
            g.set(yaw, v('Camera64.Yaw')), g.set(last, g.op('subtract', v('SM64.SimulationFrame'), 1))]
    def focus_nodes():
        return [g.set(v('Camera64.FocusX'), g.op('add', v('Camera64.FocusX'),
                      g.op('multiply', g.op('subtract', v('SM64.PosX'), v('Camera64.FocusX')), .2))),
                g.set(v('Camera64.FocusY'), g.op('add', v('Camera64.FocusY'),
                      g.op('multiply', g.op('subtract', g.op('add', v('SM64.PosY'), 1.25), v('Camera64.FocusY')), .2))),
                g.set(v('Camera64.FocusZ'), g.op('add', v('Camera64.FocusZ'),
                      g.op('multiply', g.op('subtract', v('SM64.PosZ'), v('Camera64.FocusZ')), .2)))]
    fly1 = [*focus_nodes(),
            g.control('control_if', g.op('lt', t, 31), [
                g.set(dist, g.op('add', dist, g.op('multiply', g.op('subtract', 6, dist), .3))),
                g.set(pitch, g.op('add', pitch, g.op('divide', g.op('subtract', 22.5, pitch), 16)))]),
            g.control('control_if', g.op('gt', t, 54), [g.set(dist, .65, True), g.set(yaw, .703125, True)])]
    close = [*focus_nodes(),
             g.control('control_if_else', g.op('lt', t, 33), [
                 g.set(dist, g.op('add', dist, g.op('multiply', g.op('subtract', 8, dist), .05))),
                 g.set(pitch, g.op('add', pitch, g.op('divide', g.op('subtract', 33.75, pitch), 16)))], [
                 g.set(dist, g.op('add', dist, g.op('multiply', g.op('subtract', 2.4, dist), .4)))])]
    # Camera runs after action execution, which has already advanced the timer.
    # Initialize from the live camera once per pickup, even when its first timer is 1.
    tick = [g.control('control_if', g.op('equals', last, -1), init),
            g.control('control_if', g.op('not', g.op('equals', last, v('SM64.SimulationFrame'))), [
                g.set(last, v('SM64.SimulationFrame')),
                g.control('control_if_else', eq('BOB.Star.Kind', 1), fly1, close)])]
    def horizontal():
        return g.op('multiply', dist, g.math('cos', pitch))
    tick += [g.set(v('cam x'), g.op('add', v('SM64.PosX'), g.op('multiply', horizontal(), g.math('sin', yaw)))),
             g.set(v('cam z'), g.op('add', v('SM64.PosZ'), g.op('multiply', horizontal(), g.math('cos', yaw)))),
             g.set(v('cam y'), g.op('add', v('SM64.PosY'), g.op('multiply', dist, g.math('sin', pitch)))),
             g.call('SM64 CAMERA | finalize direct camera')]
    g.procedure('SM64 STAR | native camera', tick)

    star_cam = g.op('and', eq('SM64.Action', 'ACT_STAR_DANCE_EXIT'),
                    g.op('not', eq('BOB.Star.Kind', 7)))
    _wrap(g, 'CAMERA | update isolated', star_cam, [g.call('SM64 STAR | native camera')])

    # ---- Native course-exit timing ----
    init_star = [g.set(v('Star.VisualX'), v('SM64.PosX')),
                 g.set(v('Star.VisualY'), g.op('add', v('SM64.PosY'), .3)),
                 g.set(v('Star.VisualZ'), v('SM64.PosZ')), g.set(v('Star.VisualYaw'), 0),
                 g.set(v('Star.VisualScale'), .4),
                 g.set(v('Star.OrbitYaw'), g.op('mod', g.op('add', v('SM64.FaceYaw'), 180), 360)),
                 g.set(v('Star.OrbitDiameter'), 1)]
    start_wipe = [g.set(v('Star.ExitTransition'), 1),
                  g.set(v('Star.ExitTransitionStart'), g.node('sensing_timer')),
                  g.set(v('Star.NativeReturnPending'), 1)]
    dance = [g.set(v('SM64.FaceYaw'), v('Camera64.Yaw')),
             g.set(v('SM64.ForwardVel'), 0), g.set(v('SM64.VelX'), 0), g.set(v('SM64.VelY'), 0),
             g.set(v('SM64.VelZ'), 0), g.set(v('SM64.SlideVelX'), 0), g.set(v('SM64.SlideVelZ'), 0),
             g.set(v('SM64.Grounded'), 1),
             g.set(v('SM64.PosY'), v('SM64.FloorHeight')), g.set(v('SM64.RenderY'), v('SM64.FloorHeight')),
             _call(g, ANIM, ['Other Get star', 0, 1]),
             g.control('control_if', g.op('equals', t, 0), init_star),
             g.call('SM64 STAR | update celebration star'),
             g.control('control_if', g.op('and', g.op('gt', t, 79), eq('Star.ExitTransition', 0)), start_wipe),
             g.control('control_if', g.op('and', g.op('gt', t, 111), eq('Star.NativeReturnPending', 1)), [
                 g.set(v('Level.Request'), 'STAR_RETURN')])]
    _wrap(g, 'SM64 STAR | update dance', g.op('not', eq('BOB.Star.Kind', 7)), dance)

    # ---- Painting exit success actions ----
    airborne = [g.set(v('SM64.Grounded'), 0),
        g.control('control_if', g.op('gt', t, 15), [
            _call(g, ANIM, ['SM64 General Fall (decomp)', 1, 1]),
            _call(g, 'SM64 MOVE | set forward velocity %s', [-32]),
            g.call('SM64 STEP | perform air step'),
            g.control('control_if', eq('SM64.Landed', 1), [
                g.set(v('SM64.HealCounter'), 31), *_set_cutscene_action(g, 'ACT_EXIT_LAND_SAVE_DIALOG')])])]
    g.procedure('SM64 STAR EXIT | painting airborne', airborne)
    landing = [g.set(v('SM64.Grounded'), 1),
               g.set(v('SM64.ForwardVel'), g.op('multiply', v('SM64.ForwardVel'), .9)),
               g.control('control_if', g.op('lt', g.op('multiply', v('SM64.ForwardVel'), v('SM64.ForwardVel')), 1),
                         [g.set(v('SM64.ForwardVel'), 0)]),
               _call(g, 'SM64 MOVE | set forward velocity %s', [v('SM64.ForwardVel')]),
               g.control('control_if', eq('SM64.FloorFound', 1), [g.set(v('SM64.PosY'), v('SM64.FloorHeight')),
                                                                 g.set(v('SM64.RenderY'), v('SM64.FloorHeight'))]),
               _call(g, ANIM, ['Fall from height', 0, 1]),
               g.control('control_if', eq('Anim.End', 1), [_call(g, SET_ACTION, ['ACT_IDLE', 0])])]
    g.procedure('SM64 STAR EXIT | painting land', landing)
    # Outer dispatch keeps all prior cutscene handling intact.
    _wrap(g, 'SM64 ACTION | cutscene', eq('SM64.Action', 'ACT_EXIT_AIRBORNE'),
          [g.call('SM64 STAR EXIT | painting airborne')])
    _wrap(g, 'SM64 ACTION | cutscene', eq('SM64.Action', 'ACT_EXIT_LAND_SAVE_DIALOG'),
          [g.call('SM64 STAR EXIT | painting land')])


def _world_return_logic(project):
    stage = next(t for t in project['targets'] if t.get('isStage'))
    world = next(t for t in project['targets'] if t['name'] == 'World + Assets')
    g = Graph(world, stage)
    if any(b.get('mutation', {}).get('proccode') == 'WORLD | finish native star return'
           for b in g.blocks.values()):
        return
    v = g.var
    # The existing STAR_RETURN branch still builds the proven lobby/collision scene.
    # This tail only replaces its teleport/door-reveal handoff with native painting exit.
    body = [
        g.set(v('Star.NativeReturnPending'), 0), g.set(v('Star.ExitTransition'), 0),
        g.set(v('Star.ReturnTransition'), 1), g.set(v('Star.ReturnTransitionStart'), g.node('sensing_timer')),
        g.set(v('Door.State'), 0), g.set(v('Door.Timer'), 0),
        g.set(v('Door.IrisFrame'), 0), g.set(v('Door.RevealActive'), 0),
        g.set(v('SM64.PosX'), -54.3), g.set(v('SM64.PosY'), 5.916), g.set(v('SM64.RenderY'), 5.916),
        g.set(v('SM64.PosZ'), -4.6), g.set(v('SM64.FaceYaw'), 270),
        g.set(v('SM64.VelX'), 0), g.set(v('SM64.VelY'), 0), g.set(v('SM64.VelZ'), 0),
        g.set(v('SM64.ForwardVel'), 0), g.set(v('SM64.SlideVelX'), 0), g.set(v('SM64.SlideVelZ'), 0),
        g.set(v('SM64.Grounded'), 0), g.set(v('SM64.Landed'), 0),
        g.set(v('SM64.FloorFound'), 1), g.set(v('SM64.FloorHeight'), 3.07),
        *_set_cutscene_action(g, 'ACT_EXIT_AIRBORNE'), g.set(v('Rig.UseRootMotion'), 1),
        # Reuse the already accepted painting-exit camera path (the same successful castle viewpoint).
        g.set(v('SM64.DeathExitActive'), 1), g.set(v('SM64.DeathExitStartFrame'), v('SM64.SimulationFrame')),
        g.set(v('SM64.DeathExitLastSimFrame'), g.op('subtract', v('SM64.SimulationFrame'), 1)),
        g.set(v('SM64.DeathExitElapsed'), 0),
        g.set(v('SM64.DeathExitAnchorX'), -54.3), g.set(v('SM64.DeathExitAnchorY'), 5.916),
        g.set(v('SM64.DeathExitAnchorZ'), -4.6), g.set(v('SM64.DeathExitCamOffsetX'), 2.58),
        g.set(v('SM64.DeathExitCamOffsetZ'), 11.89), g.set(v('SM64.DeathExitCamY'), 3.67),
        g.set(v('SM64.DeathExitFocusX'), -49.86), g.set(v('SM64.DeathExitFocusY'), 4.366),
        g.set(v('SM64.DeathExitFocusZ'), -3.95), g.set(v('SM64.DeathExitTargetY'), 4.366),
        g.set(v('cam x'), -42.41), g.set(v('cam y'), 3.67), g.set(v('cam z'), -2.02),
        g.set(v('Camera64.FocusX'), -49.86), g.set(v('Camera64.FocusY'), 4.366),
        g.set(v('Camera64.FocusZ'), -3.95), g.set(v('Camera64.JustInherited'), 1),
    ]
    g.procedure('WORLD | finish native star return', body)
    cond = g.op('and', g.op('equals', v('Star.NativeReturnPending'), 1),
                g.op('and', g.op('equals', v('Level.ID'), 'LOBBY'), g.op('equals', v('Star.Returning'), 1)))
    _tail(g, 'WORLD | process level request core', [g.control('control_if', cond,
                                                               [g.call('WORLD | finish native star return')])])


def _coin_shadows(project):
    stage = next(t for t in project['targets'] if t.get('isStage'))
    render = next(t for t in project['targets'] if t['name'] == 'Render')
    g = Graph(render, stage)
    marker = 'native_coin_shadow_guard_v1'
    if marker in g.blocks:
        return
    definition = _definition(g, DRAW_OBJECT)
    # Locate the normal low-level draw inside the model-present guard.
    guard = g.blocks[g.blocks[definition]['next']]['next']
    if g.blocks[guard]['opcode'] != 'control_if':
        raise ValueError('Unexpected draw-object graph while adding coin shadows')
    normal = g.blocks[guard]['inputs']['SUBSTACK'][1]
    if g.blocks[normal]['opcode'] != 'procedures_call':
        raise ValueError('Draw-object low-level call not found')

    # The newer patch already creates floor-projected red-coin shadow objects.
    # Keep its native shape and avoid a second recursive shadow beneath each coin.
    separate_red_shadows = bool(project.get('extensionData', {}).get('red_coin_shadows_v30'))
    scene_name = g.item('Scene', g.op('add', g.var('Object.Idx'), 3))
    yellow = g.op('and', _contains(g, scene_name, 'BOB.Yellow.'),
                  _contains(g, _arg(g, 'model'), 'BOB Yellow Coin Frame'))

    def shadow_call(scale):
        x, y, z = _arg(g, 'x'), _arg(g, 'y'), _arg(g, 'z')
        return _call(g, DRAW_OBJECT, ['Mario Shadow', x, g.op('add', y, .015), z,
                                     0, 0, 0, scale, 1, scale, ''])

    if separate_red_shadows:
        shadow_guard = g.control('control_if', yellow, [shadow_call(.5)])
    else:
        red = g.op('and',
            _contains(g, g.item('Scene', g.op('add', g.var('Object.Idx'), 3)), 'BOB Red Coin '),
            _contains(g, _arg(g, 'model'), 'Red Coin Frame'))
        shadow_guard = g.control('control_if_else', yellow, [shadow_call(.5)], [
            g.control('control_if', red, [shadow_call(.8)])])
    g.blocks[marker] = g.blocks.pop(shadow_guard)
    shadow_guard = marker
    # Re-parent reporters moved with the block id.
    for value in g.blocks[shadow_guard].get('inputs', {}).values():
        if len(value) > 1 and isinstance(value[1], str) and value[1] in g.blocks:
            g.blocks[value[1]]['parent'] = shadow_guard
    # Append inside the existing model-present guard, after the real coin.
    g.blocks[normal]['next'] = shadow_guard
    g.blocks[shadow_guard]['parent'] = normal

    # Recursive Mario-Shadow draw uses the coin's Scene slot. Force the native
    # standard-coin shadow alpha (0xB4 = 180) instead of Mario's dynamic alpha.
    low = _definition(g, 'RENDER | draw model low level %s type %s position %s at %s %s %s rotation %s %s %s scale %s %s %s billboard %s bone model %s shadow %s')
    tail = g.blocks[low]['next']
    while g.blocks[tail].get('next'):
        # Insert immediately before draw-current-object so opacity is final.
        if g.blocks[g.blocks[tail]['next']]['opcode'] == 'procedures_call' and \
           g.blocks[g.blocks[tail]['next']].get('mutation', {}).get('proccode') == 'RENDER | draw current object':
            break
        tail = g.blocks[tail]['next']
    name = g.item('Scene', g.op('add', g.var('Object.Idx'), 3))
    standard_coin = _contains(g, name, 'BOB.Yellow.')
    if not separate_red_shadows:
        standard_coin = g.op('or', standard_coin,
                             _contains(g, g.item('Scene', g.op('add', g.var('Object.Idx'), 3)), 'BOB Red Coin '))
    g.insert_after(tail, [g.control('control_if', g.op('and', g.op('equals', g.var('Render.ShadowMode'), 1), standard_coin),
                                      [g.set(g.var('Render.ObjectOpacity'), 180)])])


def apply_native_star_exit(project):
    meta = project.setdefault('extensionData', {})
    if meta.get('native_star_exit_v1', {}).get('version') == 1:
        return meta['native_star_exit_v1']
    _ensure_transition_placeholders(project)
    _transition_logic(project)
    _mario_star_logic(project)
    _world_return_logic(project)
    _coin_shadows(project)
    report = {
        'version': 1,
        'star_dance_camera': 'BOB native fly-away for star 1; native close-up timing for other exit stars, including Chain Chomp',
        'chain_chomp_star_exit': 'pickup commits kind 6, replacing any previous kind 7; refreshes camera/wipe state',
        'camera_initialization': 'live gameplay viewpoint on first camera update after each pickup',
        'course_exit_wipe': '32-frame ROM Mario-head textured wipe to black',
        'castle_return': '16-frame black star reveal followed by painting-exit airborne/landing action',
        'cap_sequence': 'not enabled: current single-mesh Scratch rig has no capless/head/hand-cap model routing',
        'coin_shadows': (
            'static BOB yellow coins use radial shadows at size 50 and alpha 0xB4; '
            'red coins retain the newer floor-projected native size-80 shadows without duplicates'
            if meta.get('red_coin_shadows_v30') else
            'standard static BOB yellow/red coins use project radial shadow at native sizes 50/80 and alpha 0xB4'),
        'loot_coin_shadows': 'omitted to match no-shadow moving-coin variants and avoid extra scene objects',
    }
    meta['native_star_exit_v1'] = report
    return report
