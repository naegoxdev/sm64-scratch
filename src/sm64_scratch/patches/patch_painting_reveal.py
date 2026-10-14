"""Present a completed BOB arrival image before a lightweight star reveal.

Only the painting reveal pins the pen image. Readiness requires the complete
scene constructor, a subsequent BOB camera pass and the asset update. Door,
death and teleport transitions continue using the live renderer.
"""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition, _replace_body
from sm64_scratch.patches.patch_bob_objectives import _tail, _wrap
from sm64_scratch.patches.patch_game_presentation import _prepend


def apply_painting_reveal(project):
    meta = project.setdefault('extensionData', {})
    if meta.get('painting_reveal_v26'):
        return meta['painting_reveal_v26']
    stage = next(t for t in project['targets'] if t['isStage'])
    targets = {t['name']: t for t in project['targets']}
    wg = Graph(targets['World + Assets'], stage)
    mg = Graph(targets['Mario'], stage)
    rg = Graph(targets['Render'], stage)
    tg = Graph(targets['Transitions'], stage)
    names = ('Painting.WorldReady', 'Painting.CameraReady',
             'Painting.AssetsReady', 'Painting.RevealPinned',
             'Painting.WhiteFrameShown')
    for name in names:
        wg.var(name)

    def opening(g):
        return g.op('and', g.op('equals', g.var('Painting.TransitionPhase'), 2),
                    g.op('equals', g.var('Level.ID'), 'BOB'))

    def pinned(g):
        return g.op('and', opening(g), g.op('equals', g.var('Painting.RevealPinned'), 1))

    # Acknowledge a fully white display before the expensive level constructor.
    # Otherwise the last partial fade can remain on screen throughout loading.
    _prepend(mg, 'ultimate_b_39000013', [mg.set(mg.var('Painting.WhiteFrameShown'), 0)])
    awaiting_white = wg.op('and', wg.op('equals', wg.var('Level.Request'), 'BOB'),
        wg.op('and', wg.op('equals', wg.var('Painting.TransitionPhase'), 1),
                    wg.op('equals', wg.var('Painting.WhiteFrameShown'), 0)))
    _wrap(wg, 'WORLD | process level request core', awaiting_white, [])
    _prepend(wg, 'ultimate_b_37000044',
             [wg.set(wg.var(name), 0) for name in names[:-1]]
             + [wg.set(wg.var('Painting.ScenePresented'), 0),
                wg.set(wg.var('Painting.RevealStart'), -1)])
    _tail(wg, 'WORLD | build Bob-omb Battlefield scene',
          [wg.set(wg.var('Painting.WorldReady'), 1)])

    # Loading happens in the scene phase, after the frame's camera phase.
    # Only a following camera pass can safely acknowledge the new view.
    _tail(mg, 'FRAME | update camera', [mg.control('control_if',
        mg.op('and', opening(mg), mg.op('equals', mg.var('Painting.WorldReady'), 1)),
        [mg.set(mg.var('Painting.CameraReady'), 1)])])
    _tail(wg, 'FRAME | update assets', [wg.control('control_if',
        wg.op('and', opening(wg), wg.op('equals', wg.var('Painting.WorldReady'), 1)),
        [wg.set(wg.var('Painting.AssetsReady'), 1)])])

    # Hold the destination simulation during the reveal. Reset the accumulator
    # so the held interval is not replayed as a burst of physics on resuming.
    _wrap(mg, 'MARIO | update gameplay', opening(mg),
          [mg.set(mg.var('SM64.Accumulator'), 0)])
    _wrap(mg, 'CAMERA | update', pinned(mg), [])
    _wrap(wg, 'WORLD | sync visuals', pinned(wg), [])
    _wrap(wg, 'ASSET | update loaded models', pinned(wg), [])

    ready = rg.op('and', rg.op('equals', rg.var('Painting.WorldReady'), 1),
        rg.op('and', rg.op('equals', rg.var('Painting.CameraReady'), 1),
                    rg.op('equals', rg.var('Painting.AssetsReady'), 1)))
    capture = rg.control('control_if',
        rg.op('and', rg.op('equals', rg.var('Painting.RevealPinned'), 0), ready),
        [rg.call('RENDER | draw scene'), rg.set(rg.var('Painting.RevealPinned'), 1)])
    full_white = rg.op('and', rg.op('equals', rg.var('Painting.TransitionPhase'), 1),
                      rg.op('not', rg.op('lt', rg.var('Painting.Fade'), 100)))
    _replace_body(rg, 'RENDER | draw scene or reuse wipe image', [
        rg.control('control_if_else', opening(rg), [capture],
            [rg.control('control_if', rg.op('not', full_white), [rg.call('RENDER | draw scene')])])])

    phase = tg.var('Painting.TransitionPhase')
    prepared = tg.var('Painting.ScenePresented')
    start = tg.var('Painting.RevealStart', -1)
    elapsed = tg.var('Painting.StarRevealElapsed')
    frame = tg.var('Painting.StarRevealFrame')
    last = tg.var('Painting.MaskDrawn', -1)

    def white():
        return [tg.node('looks_cleargraphiceffects'),
                tg.node('looks_switchcostumeto', {'COSTUME': 1}), tg.node('looks_show')]

    reveal = [
        tg.control('control_if', tg.op('equals', prepared, 0), [
            tg.set(prepared, 1), tg.set(start, tg.node('sensing_timer')),
            tg.set(last, -1)]),
        tg.set(elapsed, tg.op('subtract', tg.node('sensing_timer'), start)),
        tg.set(frame, tg.math('floor', tg.op('add', tg.op('multiply', elapsed, 30), .000001))),
        tg.control('control_if', tg.op('lt', frame, 0), [tg.set(frame, 0)]),
        tg.control('control_if_else', tg.op('lt', frame, 16), [
            tg.control('control_if', tg.op('not', tg.op('equals', frame, last)), [
                tg.node('looks_cleargraphiceffects'),
                tg.node('looks_switchcostumeto', {'COSTUME': tg.op('add', frame, 2)}),
                tg.node('looks_show'), tg.set(last, frame)])], [
            tg.node('looks_hide'), tg.set(phase, 0),
            tg.set(tg.var('Painting.Fade'), 0), tg.set(prepared, 0),
            tg.set(tg.var('Painting.RevealPinned'), 0)])]
    reset = [tg.node('looks_hide'), tg.set(prepared, 0), tg.set(start, -1),
             tg.set(last, -1), tg.set(tg.var('Painting.RevealPinned'), 0)]
    fade = white() + [tg.node('looks_seteffectto',
        {'VALUE': tg.op('subtract', 100, tg.var('Painting.Fade'))},
        {'EFFECT': ['GHOST', None]}),
        tg.control('control_if', tg.op('not', tg.op('lt', tg.var('Painting.Fade'), 100)),
                   [tg.set(tg.var('Painting.WhiteFrameShown'), 1)])]
    _replace_body(tg, 'WIPE | present painting star', [
        tg.control('control_if_else', tg.op('equals', phase, 0), reset, [
            tg.node('motion_gotoxy', {'X': 0, 'Y': 0}),
            tg.node('looks_setsizeto', {'SIZE': 100}),
            tg.node('looks_gotofrontback', fields={'FRONT_BACK': ['front', None]}),
            tg.control('control_if_else', tg.op('equals', phase, 1), fade, [
                tg.control('control_if_else', tg.op('equals', tg.var('Painting.RevealPinned'), 1),
                           reveal, white() + [tg.set(prepared, 0), tg.set(start, -1)])])])])
    tg.hat('event_whenflagclicked', [tg.set(tg.var(name), 0) for name in names])
    meta['painting_reveal_v26'] = dict(version=1,
        ready='Full white acknowledged; BOB scene constructor, subsequent camera and asset passes complete before capture',
        reveal='One completed destination pen image; native 16/30-second wall clock; mask-only frames',
        simulation='Hold BOB simulation and pinned scene/camera during reveal; discard held physics accumulator',
        scope='Painting entry only; door/death/teleport keep live rendering',
        seated_contact='Decoded pelvis contact for slide clip 145; full-body contact for stop clip 143',
        runtime_verified=False)
    return meta['painting_reveal_v26']
