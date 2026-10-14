"""Ground-pound phases, native wipe scheduling, death return and coin cues."""
from copy import deepcopy
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition, _call, _replace_body
from sm64_scratch.patches.patch_game_features import _sprite, _sound


def hide_fps(project):
    """Keep timing data internal; no FPS monitor can be made visible."""
    removed = 0
    for target in project['targets']:
        for block in target['blocks'].values():
            if block['opcode'] == 'data_showvariable' and 'fps' in block.get('fields', {}).get('VARIABLE', [''])[0].lower():
                block['opcode'] = 'data_hidevariable';removed += 1
    project['monitors'] = [m for m in project.get('monitors', []) if 'fps' not in str(m.get('params', {})).lower() and 'fps' not in str(m.get('id', '')).lower()]
    return removed


def _broadcast(g, name):
    identity = 'gameplay_v13_' + name.lower().replace(' ', '_')
    g.stage['broadcasts'][identity] = name
    node = g.node('event_broadcast')
    g.blocks[node]['inputs'] = {'BROADCAST_INPUT': [1, [11, name, identity]]}
    return node


def apply_gameplay_fixes(project):
    meta = project.setdefault('extensionData', {})
    # Repair already-generated projects as well as future Graph.math calls.
    # Scratch mathop has no "round" option and returns zero for it. This kept
    # the close mask at frame zero and the reveal mask at frame twenty, so
    # the door state machine could never acknowledge/finish those phases.
    repaired = set(meta.get('castle_door_v15', {}).get('repaired_reporters', []))
    for target in project['targets']:
        for key, block in target['blocks'].items():
            if (block['opcode'] == 'operator_mathop'
                    and block.get('fields', {}).get('OPERATOR', [''])[0] == 'round'):
                block['opcode'] = 'operator_round'
                block['fields'] = {}
                repaired.add(target['name'] + '/' + key)
    meta['castle_door_v15'] = dict(
        version=1, repaired_reporters=sorted(repaired),
        cause='Unsupported mathop round returned zero, blocking door close/reveal',
        repair='Use the dedicated operator_round reporter; preserve door timings and animations')
    stage = next(t for t in project['targets'] if t.get('isStage'))
    targets = {t['name']: t for t in project['targets']}
    hide_fps(project)
    mg = Graph(targets['Mario'], stage)
    wg = Graph(targets['World + Assets'], stage)
    rg = Graph(targets['Render'], stage)
    if 'gameplay_v13_render_live' not in rg.blocks:
        call = rg.call('RENDER | draw scene')
        rg.blocks['gameplay_v13_render_live'] = rg.blocks.pop(call)
        _replace_body(rg, 'RENDER | draw scene or reuse wipe image', ['gameplay_v13_render_live'])
        close_frame = mg.math('round', mg.op('multiply', mg.op('subtract', mg.var('Door.Timer'), 16), 20/19))
        mg.blocks['Bg']['inputs']['VALUE'] = [3, close_frame, [10, '']]
        mg.blocks[close_frame]['parent'] = 'Bg'
    # A castle reveal only changes the 2D iris after the destination scene has
    # been loaded. Let the new camera and scene settle for two complete renders
    # under the black mask, then keep the third destination frame while the
    # iris opens. This avoids capturing a one-frame camera pose from the room
    # transition while still avoiding a full 3D redraw on every mask frame.
    # Mario, physics, camera, world and assets still update on every tick.
    if 'gameplay_v13_door_cache_gate' not in rg.blocks:
        scene_presented = rg.var('Door.ScenePresented', 0)
        door_opening = rg.op('and', rg.op('equals', rg.var('Door.State'), 2),
                             rg.op('equals', rg.var('Door.RevealActive'), 1))
        keep_frame = rg.op('not', rg.op('and', door_opening,
                                       rg.op('equals', scene_presented, 1)))
        gate = rg.control('control_if', keep_frame, ['gameplay_v13_render_live'])
        rg.blocks['gameplay_v13_door_cache_gate'] = rg.blocks.pop(gate)
        _replace_body(rg, 'RENDER | draw scene or reuse wipe image',
                      ['gameplay_v13_door_cache_gate'])
    if 'gameplay_v13_door_cache_latch' not in targets['Door Transition']['blocks']:
        dg = Graph(targets['Door Transition'], stage)
        scene_presented = dg.var('Door.ScenePresented', 0)
        warmup = dg.var('Door.SceneWarmup', 0)
        opening = dg.op('and', dg.op('equals', dg.var('Door.State'), 2),
                        dg.op('equals', dg.var('Door.RevealActive'), 1))
        latch = dg.control('control_if_else', opening, [
            dg.control('control_if_else', dg.op('lt', warmup, 2),
                [dg.set(scene_presented, 0), dg.set(warmup, 1, True)],
                [dg.set(scene_presented, 1)])],
            [dg.set(scene_presented, 0), dg.set(warmup, 0)])
        dg.blocks['gameplay_v13_door_cache_latch'] = dg.blocks.pop(latch)
        definition = _definition(dg, 'WIPE | present door circle')
        old_first = dg.blocks[definition]['next']
        dg.blocks[definition]['next'] = 'gameplay_v13_door_cache_latch'
        dg.blocks['gameplay_v13_door_cache_latch']['parent'] = definition
        dg.blocks['gameplay_v13_door_cache_latch']['next'] = old_first
        if old_first:
            dg.blocks[old_first]['parent'] = 'gameplay_v13_door_cache_latch'
        meta['castle_transition_cache'] = {
            'version': 2,
            'scope': 'Door.State == 2 and Door.RevealActive == 1',
            'scene_renders': 3,
            'camera_warmup_frames': 2,
            'simulation_camera_world_assets': 'continue updating during reveal and warm-up',
        }
    if meta.get('game_presentation_fix'):
        meta['game_presentation_fix'].update(
            door_warp='Full black frame acknowledged; one render-side 16/30 second reveal clock',
            painting_wipe='ROM star mask over the live scene for 16/30 seconds')
    if meta.get('gameplay_v13'):
        meta['gameplay_v13'].update(version=4,
                                    door_reveal='Two camera warm-up renders under black, then reuse the settled third frame while the 16-frame iris opens',
                                    painting_reveal='ROM white star mask; scene continues animating underneath')
    if 'gameplay_v13_ground_pound_dispatch' in mg.blocks and 'Death Transition' in targets:
        return meta['gameplay_v13']
    # Native start clips 60/59 are distinct from the held falling pose 61.
    threshold = lambda: mg.op('add', 15, mg.op('multiply', 5, mg.op('equals', mg.var('SM64.ActionArg'), 1)))
    request = lambda name: _call(mg, 'ANIM | request %s loop %s speed %s', [name, 0, 1])
    start = mg.control('control_if_else', mg.op('equals', mg.var('SM64.ActionArg'), 1),
                       [request('SM64 Triple Jump Ground Pound Start')], [request('SM64 Ground Pound Start')])
    dispatch = mg.control('control_if_else', mg.op('lt', mg.var('SM64.ActionTimer'), threshold()),
                          [start], [request('Ground pound part')])
    previous = deepcopy(mg.blocks['WK'])
    mg.blocks['gameplay_v13_ground_pound_dispatch'] = mg.blocks.pop(dispatch)
    replacement = mg.blocks['gameplay_v13_ground_pound_dispatch']
    replacement.update(parent=previous['parent'], next=previous['next'])
    parent = mg.blocks[previous['parent']]
    if parent.get('next') == 'WK':parent['next'] = 'gameplay_v13_ground_pound_dispatch'
    else:
        for value in parent['inputs'].values():
            if len(value) > 1 and value[1] == 'WK':value[1] = 'gameplay_v13_ground_pound_dispatch'
    mg.blocks[previous['next']]['parent'] = 'gameplay_v13_ground_pound_dispatch'
    del mg.blocks['WK']
    for value in replacement['inputs'].values():
        if len(value)>1 and isinstance(value[1], str) and value[1] in mg.blocks:mg.blocks[value[1]]['parent'] = 'gameplay_v13_ground_pound_dispatch'
    condition = mg.op('lt', mg.var('SM64.ActionTimer'), threshold())
    mg.blocks['ep']['inputs']['CONDITION'] = [2, condition];mg.blocks[condition]['parent'] = 'ep'
    mg.insert_after('XA', [mg.set(mg.var('SM64.GroundPoundPhase'), 0)])

    # One render-side clock owns the door reveal. The old world clock could
    # clear RevealActive before the sprite reached its fully open costume.
    _replace_body(wg, 'WORLD | update castle entrance reveal', [])
    dg = Graph(targets['Door Transition'], stage)
    start = dg.var('Door.RevealStart', -1);frame = dg.var('Door.MaskFrame');closed = dg.var('Door.ClosedFrameShown')
    opening = lambda: dg.op('and', dg.op('equals', dg.var('Door.State'), 2), dg.op('equals', dg.var('Door.RevealActive'), 1))
    elapsed = lambda: dg.op('subtract', dg.node('sensing_timer'), start)
    _replace_body(dg, 'WIPE | present door circle', [
        dg.control('control_if_else', opening(), [
            dg.control('control_if_else', dg.op('equals', dg.var('Door.ScenePresented'), 1), [
                dg.control('control_if', dg.op('lt', start, 0), [dg.set(start, dg.node('sensing_timer'))]),
                dg.set(frame, dg.op('subtract', 20, dg.math('round', dg.op('multiply', 20*30/16, elapsed())))),
                dg.control('control_if', dg.op('lt', frame, 0), [dg.set(frame, 0)]),
                dg.set(dg.var('Door.IrisFrame'), frame)],
                [dg.set(start, -1), dg.set(frame, 20), dg.set(dg.var('Door.IrisFrame'), 20)])],
            [dg.set(start, -1), dg.set(frame, dg.var('Door.IrisFrame'))]),
        dg.control('control_if_else', dg.op('gt', frame, 0), [
            dg.node('motion_gotoxy', {'X':0, 'Y':0}), dg.node('looks_setsizeto', {'SIZE':100}),
            dg.node('looks_gotofrontback', fields={'FRONT_BACK':['front',None]}),
            dg.node('looks_switchcostumeto', {'COSTUME':dg.op('add', frame, 1)}),
            dg.node('looks_seteffectto', {'VALUE':0}, {'EFFECT':['GHOST',None]}), dg.node('looks_show'),
            dg.control('control_if', dg.op('equals', frame, 20), [dg.set(closed, 1)])],
            [dg.node('looks_hide'), dg.set(closed, 0),
             dg.control('control_if', opening(), [dg.set(dg.var('Door.RevealActive'), 0)])])])
    # A fresh BASE reaches this code after the transition procedure is
    # rebuilt above, so reconnect the frame-complete cache latch if needed.
    latch_key = 'gameplay_v13_door_cache_latch'
    if latch_key not in dg.blocks:
        scene_presented = dg.var('Door.ScenePresented', 0)
        warmup = dg.var('Door.SceneWarmup', 0)
        latch_opening = dg.op('and', dg.op('equals', dg.var('Door.State'), 2),
                              dg.op('equals', dg.var('Door.RevealActive'), 1))
        latch = dg.control('control_if_else', latch_opening, [
            dg.control('control_if_else', dg.op('lt', warmup, 2),
                [dg.set(scene_presented, 0), dg.set(warmup, 1, True)],
                [dg.set(scene_presented, 1)])],
            [dg.set(scene_presented, 0), dg.set(warmup, 0)])
        dg.blocks[latch_key] = dg.blocks.pop(latch)
    definition = _definition(dg, 'WIPE | present door circle')
    if dg.blocks[definition].get('next') != latch_key:
        old_first = dg.blocks[definition].get('next')
        dg.blocks[definition]['next'] = latch_key
        dg.blocks[latch_key]['parent'] = definition
        dg.blocks[latch_key]['next'] = old_first
        if old_first:
            dg.blocks[old_first]['parent'] = latch_key
    # Fix the position/scale explicitly for migrated overlay sprites as well.
    for name in ('Door Transition', 'Transitions'):
        targets[name].update(x=0, y=0, size=100, direction=90, rotationStyle="don't rotate")

    phase = wg.var('Death.WipePhase', 0);pending = wg.var('Death.PendingLevel', '')
    start = wg.var('Death.WipeStart', 0);closed = wg.var('Death.ClosedFrameShown', 0)
    prepared = wg.var('Death.ScenePresented', 0)
    definition = _definition(wg, 'WORLD | process level request')
    proto = wg.blocks[wg.blocks[definition]['inputs']['custom_block'][1]]
    proto['mutation']['proccode'] = 'WORLD | process level request core'
    has_request = lambda: wg.op('not', wg.op('equals', wg.var('Level.Request'), ''))
    dying = lambda: wg.op('or', wg.op('equals', wg.var('Level.Request'), 'DEATH_RETURN'), wg.op('or',
              wg.op('equals', wg.var('SM64.Action'), 'ACT_STANDING_DEATH'), wg.op('equals', wg.var('SM64.Action'), 'ACT_DROWNING')))
    wg.procedure('WORLD | process level request', [
        wg.control('control_if_else', wg.op('equals', phase, 1), [
            wg.set(wg.var('Level.Request'), ''),
            wg.control('control_if', wg.op('and', wg.op('equals', closed, 1), wg.op('not', wg.op('lt', wg.op('subtract', wg.node('sensing_timer'), start), 48/30))), [
                wg.set(wg.var('Level.Request'), pending), wg.call('WORLD | process level request core'),
                wg.set(phase, 2), wg.set(prepared, 0)])], [
            wg.control('control_if_else', wg.op('and', wg.op('equals', phase, 0), wg.op('and', has_request(), dying())), [
                wg.set(pending, wg.var('Level.Request')), wg.set(wg.var('Level.Request'), ''), wg.set(phase, 1),
                wg.set(start, wg.node('sensing_timer')), wg.set(closed, 0),
                _broadcast(wg, 'DEATH BOWSER WIPE')], [wg.call('WORLD | process level request core')])])])
    wg.hat('event_whenflagclicked', [wg.set(phase, 0), wg.set(closed, 0), wg.set(prepared, 0), wg.set(pending, '')])

    death, _, g = _sprite(project, 'Death Transition')
    placeholder = deepcopy(death['costumes'][0])
    death['costumes'] = [dict(placeholder, name='Bowser %02d' % i, bitmapResolution=1, rotationCenterX=240, rotationCenterY=180) for i in range(48)]
    death['costumes'] += [dict(placeholder, name='Death reveal %02d' % i, bitmapResolution=1, rotationCenterX=240, rotationCenterY=180) for i in range(17)]
    death.update(rotationStyle="don't rotate")
    phase = g.var('Death.WipePhase');start = g.var('Death.WipeStart');closed = g.var('Death.ClosedFrameShown');prepared = g.var('Death.ScenePresented')
    frame = g.var('Death.MaskFrame', 0, local=True);elapsed = lambda: g.op('subtract', g.node('sensing_timer'), start)
    present = lambda: [g.node('motion_gotoxy', {'X':0,'Y':0}), g.node('looks_setsizeto', {'SIZE':100}),
                      g.node('looks_seteffectto', {'VALUE':0}, {'EFFECT':['GHOST',None]}),
                      g.node('looks_gotofrontback', fields={'FRONT_BACK':['front',None]}),
                      g.node('looks_switchcostumeto', {'COSTUME':frame}), g.node('looks_show')]
    g.procedure('WIPE | present death and return', [g.control('control_if_else', g.op('equals', phase, 1), [
        g.set(frame, g.op('add', 1, g.math('floor', g.op('multiply', 30, elapsed())))),
        g.control('control_if', g.op('gt', frame, 48), [g.set(frame, 48)]), *present(),
        g.control('control_if', g.op('equals', frame, 48), [g.set(closed, 1)])], [
        g.control('control_if_else', g.op('equals', phase, 2), [
            g.control('control_if', g.op('equals', prepared, 0), [g.set(prepared, 1), g.set(start, g.node('sensing_timer'))]),
            g.set(frame, g.op('add', 49, g.math('floor', g.op('multiply', 30, elapsed())))),
            g.control('control_if_else', g.op('lt', frame, 65), present(), [g.node('looks_hide'), g.set(phase, 0)])],
            [g.node('looks_hide')])])])
    g.hat('event_whenflagclicked', [g.node('looks_hide')])
    g.hat('event_whenbroadcastreceived', [g.call('WIPE | present death and return')], ['ENGINE FRAME RENDERED', 'porter_frame_rendered'])

    # Sound cues are dispatched at the actual physics branches, so slow
    # observer sampling cannot miss a short coin spawn or floor bounce.
    index = wg.var('BOB.Goomba.I');count = 'BOB.Goomba.CoinBounceCount';wg.list(count)
    ensure = [wg.control('control_repeat', wg.op('subtract', index, wg.length(count)), [wg.node('data_addtolist', {'ITEM':0}, {'LIST':wg.list(count)})]),
              wg.node('data_replaceitemoflist', {'INDEX':index,'ITEM':0}, {'LIST':wg.list(count)}), _broadcast(wg, 'COIN SPURT')]
    wg.insert_after('v12_datareplaceitemoflist_253', ensure)
    bounce = wg.control('control_if', wg.op('and', wg.op('gt', wg.item('BOB.Goomba.CoinVelY', index), 0), wg.op('lt', wg.item(count, index), 5)), [
        _broadcast(wg, 'COIN BOUNCE'), wg.node('data_replaceitemoflist', {'INDEX':index,'ITEM':wg.op('add', wg.item(count,index),1)}, {'LIST':wg.list(count)})])
    wg.insert_after('v12_datareplaceitemoflist_297', [bounce])
    wg.insert_after(_definition(wg, 'GOOMBA | create BOB actors'), [wg.node('data_deletealloflist', fields={'LIST':wg.list(count)})])
    report = dict(version=3, fps_monitors='Removed; show-variable blocks changed to hide',
                  ground_pound='Native start 60/59, held descent 61, authored seated impact 58',
                  door_reveal='Two black-mask camera warm-up renders, then hold a settled frame during the 16/30 second iris',
                  painting_reveal='ROM white star mask; scene continues animating underneath',
                  death='48/30 second Bowser mask before loading; 16/30 second star reveal on return',
                  coins='ROM spurt cue at spawn and drop cue on first five physical bounces')
    meta['gameplay_v13'] = report
    return report
