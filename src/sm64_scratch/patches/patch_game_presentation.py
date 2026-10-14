"""Atomic gameplay wipes, a low pipe-rise view, and native camera cues.

Only Scratch logic is generated here. Sound samples still come from the ROM.
The native pipe spline is kept intact; the additional rise, which precedes
that spline in the game, gets a separate low framing correction.
"""
import copy
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_game_features import _sprite, _sound
from sm64_scratch.decoders.decode_game_audio import GAME_AUDIO_DISPLAY_NAMES

CAMERA_SOUNDS = ('turn', 'zoom_in', 'zoom_out', 'buzz')


def _definition(g, code):
    return next(k for k, b in g.blocks.items() if b['opcode'] == 'procedures_definition'
                and g.blocks[b['inputs']['custom_block'][1]]['mutation']['proccode'] == code)


def _prepend(g, anchor, body):
    """Insert before an existing statement without truncating its continuation."""
    parent = g.blocks[anchor]['parent']
    first = g.chain(body, parent)
    if g.blocks[parent].get('next') == anchor:
        g.blocks[parent]['next'] = first
    else:
        for inp in g.blocks[parent]['inputs'].values():
            if len(inp) > 1 and inp[1] == anchor:
                inp[1] = first
                break
        else:
            raise ValueError('Statement is not linked to its parent: ' + anchor)
    g.blocks[body[-1]]['next'] = anchor
    g.blocks[anchor]['parent'] = body[-1]


def _broadcast(g, stage, key):
    identity, label = 'porter_camera_' + key, 'CAMERA SOUND ' + key.upper()
    stage['broadcasts'][identity] = label
    node = g.node('event_broadcast')
    g.blocks[node]['inputs'] = {'BROADCAST_INPUT': [1, [11, label, identity]]}
    return node


def add_camera_audio(project):
    """Repopulate the playback sprite after the audio constructor runs."""
    records = {s['name']: copy.deepcopy(s) for t in project['targets'] for s in t['sounds']}
    target, stage, g = _sprite(project, 'Camera Audio')
    names = {GAME_AUDIO_DISPLAY_NAMES['game_camera_' + key] for key in CAMERA_SOUNDS}
    target['sounds'] = [records[name] for name in sorted(names) if name in records]
    for key in CAMERA_SOUNDS:
        g.hat('event_whenbroadcastreceived', [_sound(g, GAME_AUDIO_DISPLAY_NAMES['game_camera_' + key])],
              ['CAMERA SOUND ' + key.upper(), 'porter_camera_' + key])
    for owner in project['targets']:
        if owner is not target:
            owner['sounds'] = [s for s in owner['sounds'] if s['name'] not in names]


def apply_game_presentation(project):
    meta = project.setdefault('extensionData', {})
    if meta.get('game_presentation_fix', {}).get('version') == 1:
        add_camera_audio(project)
        return meta['game_presentation_fix']
    stage = next(t for t in project['targets'] if t['isStage'])
    targets = {t['name']: t for t in project['targets']}
    g = Graph(targets['Mario'], stage)
    # Aiming the first scene does not depend on a previous title or saved pose.
    g.blocks['#/']['inputs']['VALUE'] = [1, [4, .8]]
    closed = g.var('Door.ClosedFrameShown')
    prepared = g.var('Painting.ScenePresented')
    g.insert_after('+f', [g.set(closed, 0), g.set(prepared, 0)])
    # Keep the authored rise and waiting view below the raised pipe rim.
    # Native Y crosses below this limit before emergence, so the unchanged
    # jump/orbit path resumes without a position discontinuity at frame 119.
    g.procedure('INTRO | frame pipe rise from below', [
        g.control('control_if', g.op('and', g.op('lt', g.var('Intro.Frame'), 119),
                  g.op('gt', g.var('cam y'), 1.6)), [g.set(g.var('cam y'), 1.6)]),
        g.control('control_if', g.op('lt', g.var('Intro.Frame'), 1),
                  [g.set(g.var('Camera64.FocusY'), 2.0)]),
    ])
    _prepend(g, 'en', [g.call('INTRO | frame pipe rise from below')])
    # Timer equality could skip the closed image at low FPS. The render-side
    # latch is set only after the fully black costume has been presented.
    condition = g.op('and', g.op('gt', g.var('Door.Timer'), 35), g.op('equals', closed, 1))
    g.blocks['Bi']['inputs']['CONDITION'] = [2, condition]
    g.blocks[condition]['parent'] = 'Bi'
    # C-button sound dispatch occurs in the actual accepted edge branches,
    # before PrevLeft/Right/Up/Down are updated. A held key has no repeat cue.
    for anchor in ('cam77_b00275', 'cam77_b00280'):
        _prepend(g, anchor, [_broadcast(g, stage, 'turn')])
    for anchor, goal in (('cam77_b00231', 105), ('cam77_b00248', -105)):
        sound = g.control('control_if_else', g.op('equals', g.var('CamSM64.ModeOffsetYaw'), goal),
                          [_broadcast(g, stage, 'buzz')], [_broadcast(g, stage, 'turn')])
        _prepend(g, anchor, [sound])
    for anchor in ('cam77_b00237', 'cam77_b00254', 'cam77_b00269', 'cam77_b00293',
                   'cam77_b00270', 'cam77_b00294', 'cam77_b00221'):
        _prepend(g, anchor, [_broadcast(g, stage, 'zoom_in')])
    for anchor in ('cam77_b00262', 'cam77_b00285'):
        sound = g.control('control_if_else', g.op('equals', g.var('CamSM64.ZoomedOut'), 1),
                          [_broadcast(g, stage, 'buzz')], [_broadcast(g, stage, 'zoom_out')])
        _prepend(g, anchor, [sound])

    world = Graph(targets['World + Assets'], stage)
    world.insert_after('ultimate_b_37000044', [world.set(prepared, 0)])
    # Keep the completed new scene while its 0.533 s star mask opens. Full
    # software rasterization is unnecessary for each mask-only display frame.
    rg = Graph(targets['Render'], stage)
    calls = [b for b in rg.blocks.values() if b['opcode'] == 'procedures_call'
             and b['mutation']['proccode'] == 'RENDER | draw scene']
    for b in calls:
        b['mutation']['proccode'] = 'RENDER | draw scene or reuse wipe image'
    rg.procedure('RENDER | draw scene or reuse wipe image', [
        rg.control('control_if', rg.op('not', rg.op('and',
                   rg.op('equals', rg.var('Painting.TransitionPhase'), 2),
                   rg.op('equals', prepared, 1))), [rg.call('RENDER | draw scene')])])

    for name in ('Door Transition', 'Transitions'):
        t = targets[name]
        t['blocks'] = {}; t.update(x=0, y=0, size=100, direction=90, visible=False)
    dg = Graph(targets['Door Transition'], stage)
    start = dg.var('Door.RevealStart', -1)
    frame = dg.var('Door.MaskFrame')
    drawn = dg.var('Door.IrisDrawn')
    opening = dg.op('and', dg.op('equals', dg.var('Door.State'), 2),
                    dg.op('equals', dg.var('Door.RevealActive'), 1))
    open_body = [dg.control('control_if', dg.op('lt', start, 0),
                           [dg.set(start, dg.node('sensing_timer'))]),
                 dg.set(frame, dg.op('subtract', 20, dg.math('floor', dg.op('multiply', 30,
                           dg.op('subtract', dg.node('sensing_timer'), start))))),
                 dg.control('control_if', dg.op('lt', frame, 0), [dg.set(frame, 0)]),
                 dg.set(dg.var('Door.IrisFrame'), frame)]
    close_body = [dg.set(start, -1), dg.set(frame, dg.var('Door.IrisFrame')),
                  dg.control('control_if', dg.op('lt', frame, 20), [dg.set(closed, 0)])]
    present = [dg.node('looks_gotofrontback', fields={'FRONT_BACK': ['front', None]}),
               dg.node('looks_switchcostumeto', {'COSTUME': dg.op('add', frame, 1)}),
               dg.node('looks_seteffectto', {'VALUE': 0}, {'EFFECT': ['GHOST', None]}),
               dg.node('looks_show'), dg.set(drawn, frame),
               dg.control('control_if', dg.op('equals', frame, 20), [dg.set(closed, 1)])]
    dg.procedure('WIPE | present door circle', [dg.control('control_if_else', opening, open_body, close_body),
        dg.control('control_if_else', dg.op('gt', frame, 0), present,
                   [dg.node('looks_hide'), dg.set(drawn, 0),
                    dg.control('control_if', dg.op('equals', dg.var('Door.State'), 2),
                               [dg.set(dg.var('Door.RevealActive'), 0)])])])
    dg.hat('event_whenflagclicked', [dg.node('looks_hide'), dg.set(start, -1), dg.set(drawn, 0), dg.set(closed, 0)])
    dg.hat('event_whenbroadcastreceived', [dg.call('WIPE | present door circle')],
           ['ENGINE FRAME RENDERED', 'porter_frame_rendered'])

    tg = Graph(targets['Transitions'], stage)
    start = tg.var('Painting.RevealStart', -1)
    frame = tg.var('Painting.StarRevealFrame')
    phase = tg.var('Painting.TransitionPhase')
    elapsed = tg.var('Painting.StarRevealElapsed')
    last = tg.var('Painting.MaskDrawn', -1)
    reveal = [tg.control('control_if', tg.op('equals', prepared, 0),
                       [tg.set(prepared, 1), tg.set(start, tg.node('sensing_timer')), tg.set(last, -1)]),
              tg.set(elapsed, tg.op('subtract', tg.node('sensing_timer'), start)),
              tg.set(frame, tg.math('floor', tg.op('add', tg.op('multiply', elapsed, 30), .000001))),
              tg.control('control_if_else', tg.op('lt', frame, 16), [
                  tg.control('control_if', tg.op('not', tg.op('equals', frame, last)), [
                      tg.node('looks_seteffectto', {'VALUE': 0}, {'EFFECT': ['GHOST', None]}),
                      tg.node('looks_switchcostumeto', {'COSTUME': tg.op('add', frame, 2)}),
                      tg.node('looks_show'), tg.set(last, frame)])],
                  [tg.node('looks_hide'), tg.set(frame, 16), tg.set(elapsed, 16/30),
                   tg.set(phase, 0), tg.set(tg.var('Painting.Fade'), 0), tg.set(prepared, 0)])]
    tg.procedure('WIPE | present painting star', [
        tg.control('control_if_else', tg.op('equals', phase, 0),
                   [tg.node('looks_hide'), tg.set(prepared, 0), tg.set(start, -1), tg.set(last, -1)], [
            tg.node('looks_gotofrontback', fields={'FRONT_BACK': ['front', None]}),
            tg.control('control_if_else', tg.op('equals', phase, 1), [
                tg.set(prepared, 0), tg.set(last, -1),
                tg.node('looks_switchcostumeto', {'COSTUME': 1}),
                tg.node('looks_seteffectto', {'VALUE': tg.op('subtract', 100, tg.var('Painting.Fade'))},
                        {'EFFECT': ['GHOST', None]}), tg.node('looks_show')], reveal)])])
    tg.hat('event_whenflagclicked', [tg.node('looks_hide'), tg.set(prepared, 0), tg.set(start, -1), tg.set(last, -1)])
    tg.hat('event_whenbroadcastreceived', [tg.call('WIPE | present painting star')],
           ['ENGINE FRAME RENDERED', 'porter_frame_rendered'])
    # No wait blocks or busy loops in the render-completion handlers.
    report = dict(version=1, camera_sounds=list(CAMERA_SOUNDS),
                  door_warp='Full black frame acknowledged before the scene request; 30 Hz timed reveal',
                  painting_wipe='One new-scene render, then reuse that image for a 16/30 second timed mask',
                  pipe_rise_view='Camera height capped below the pipe rim until emergence; native height resumes continuously before frame 119',
                  native_pipe_camera_lists_unchanged=True, gameplay_camera_handoff_unchanged=True)
    meta['game_presentation_fix'] = report
    add_camera_audio(project)
    return report
