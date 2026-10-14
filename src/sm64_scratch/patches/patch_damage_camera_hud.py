"""Native damage blinking and a screen-space camera status HUD.

This module adds assetless block graphs. Camera glyphs are decoded locally
by decode_hud; blinking skips Mario's draw calls, preserving simulation.
"""
from copy import deepcopy

from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_game_features import _sprite


CAMERA_HUD_NAME = 'SM64 HUD Camera'
CAMERA_HUD_COSTUMES = tuple(
    mode + suffix for mode in ('Lakitu', 'Mario', 'Fixed')
    for suffix in ('', ' Up', ' Down'))


def apply_damage_camera_hud(project):
    meta = project.setdefault('extensionData', {})
    stage = next(t for t in project['targets'] if t.get('isStage'))
    targets = {t['name']: t for t in project['targets']}
    rg = Graph(targets['Render'], stage)
    gate_id = 'gameplay_v14_damage_blink_gate'
    if gate_id not in rg.blocks:
        # Object.Idx points to the scene's .start marker. The name value is
        # three entries later; the existing model value is seven entries later.
        draw = next(k for k, b in rg.blocks.items()
                    if b['opcode'] == 'procedures_call'
                    and b.get('mutation', {}).get('proccode', '').startswith('RENDER | draw object model')
                    and any('glue_arg_300077' == arg for arg in b.get('inputs', {})))
        original = deepcopy(rg.blocks[draw])
        scene_name = lambda: rg.item('Scene', rg.op('add', rg.var('Object.Idx'), 3))
        mario = rg.op('or', rg.op('equals', scene_name(), 'Mario'),
                      rg.op('equals', scene_name(), 'Mario Shadow'))
        odd_tick = rg.op('equals', rg.op('mod', rg.var('SM64.SimulationFrame'), 2), 1)
        blink = rg.var('Render.DamageBlink', 0)
        # Evaluate the invulnerability clock once per frame, before the queue.
        rg.insert_after('PA', [rg.set(blink, rg.op('and', rg.op('gt', rg.var('SM64.InvincTimer'), 2), odd_tick))])
        blinking = rg.op('and', mario, rg.op('equals', blink, 1))
        gate = rg.control('control_if', rg.op('not', blinking), [draw])
        rg.blocks[gate_id] = rg.blocks.pop(gate)
        node = rg.blocks[gate_id]
        node.update(parent=original['parent'], next=original['next'])
        parent = rg.blocks[original['parent']]
        if parent.get('next') == draw:
            parent['next'] = gate_id
        else:
            for value in parent.get('inputs', {}).values():
                if len(value) > 1 and value[1] == draw:
                    value[1] = gate_id
                    break
            else:
                raise ValueError('Mario rendering call is not linked to its parent')
        if original['next']:
            rg.blocks[original['next']]['parent'] = gate_id
        for value in node['inputs'].values():
            if len(value) > 1 and isinstance(value[1], str) and value[1] in rg.blocks:
                rg.blocks[value[1]]['parent'] = gate_id

    if CAMERA_HUD_NAME not in targets:
        hud, _, g = _sprite(project, CAMERA_HUD_NAME)
        placeholder = deepcopy(hud['costumes'][0])
        hud['costumes'] = [dict(placeholder, name=name, bitmapResolution=1,
                                rotationCenterX=240, rotationCenterY=180)
                           for name in CAMERA_HUD_COSTUMES]
        hud.update(x=0, y=0, size=100, visible=False,
                   rotationStyle="don't rotate",
                   layerOrder=targets['SM64 HUD']['layerOrder'] + 1)
        ready = g.var('HUD.CameraReady', 0, local=True)
        costume = g.var('HUD.CameraCostume', 'Lakitu', local=True)
        fixed_conditions = [
            g.op('equals', g.var('Intro.Active'), 1),
            g.op('gt', g.var('Door.State'), 0),
            g.op('gt', g.var('RoomDoor.State'), 0),
            g.op('gt', g.var('Painting.State'), 0),
            g.op('equals', g.var('SM64.DeathExitActive'), 1),
            g.op('equals', g.var('Chomp.CutsceneActive'), 1),
            g.op('gt', g.var('CamCastle.DoorTail'), 0),
            g.op('and', g.op('equals', g.var('Level.ID'), 'LOBBY'),
                 g.op('equals', g.var('CamCastle.Mode'), 'FIXED')),
        ]
        fixed = fixed_conditions.pop()
        for condition in fixed_conditions:
            fixed = g.op('or', fixed, condition)
        select = g.control('control_if_else', fixed, [g.set(costume, 'Fixed')], [
            g.control('control_if_else', g.op('equals', g.var('CamSM64.Mode'), 'MARIO'),
                      [g.set(costume, 'Mario')], [g.set(costume, 'Lakitu')]),
            g.control('control_if_else', g.op('equals', g.var('CamSM64.Mode'), 'C_UP'),
                      [g.set(costume, g.node('operator_join', {'STRING1':costume, 'STRING2':' Up'}))], [
                g.control('control_if', g.op('equals', g.var('CamSM64.ZoomedOut'), 1),
                          [g.set(costume, g.node('operator_join', {'STRING1':costume, 'STRING2':' Down'}))])])])
        valid = g.op('and', g.op('equals', ready, 1),
                     g.op('and', g.op('equals', g.var('TitleIntro.Active'), 0),
                          g.op('or', g.op('equals', g.var('Level.ID'), 'GROUNDS'),
                               g.op('or', g.op('equals', g.var('Level.ID'), 'LOBBY'),
                                    g.op('equals', g.var('Level.ID'), 'BOB')))))
        g.procedure('HUD | present camera status', [
            g.control('control_if_else', valid, [select,
                g.node('motion_gotoxy', {'X':0, 'Y':0}),
                g.node('looks_setsizeto', {'SIZE':100}),
                g.node('looks_seteffectto', {'VALUE':0}, {'EFFECT':['GHOST',None]}),
                g.node('looks_switchcostumeto', {'COSTUME':costume}),
                g.node('looks_show')], [g.node('looks_hide')])])
        # Stay below the wipe overlays: no go-to-front and no independent loop.
        g.hat('event_whenflagclicked', [g.set(ready, 0), g.node('looks_hide')])
        g.hat('event_whenbroadcastreceived', [g.set(ready, 0), g.node('looks_hide')],
              ['TITLE INTRO RUN', 'titleintro_bc_run'])
        g.hat('event_whenbroadcastreceived', [g.set(ready, 1)],
              ['TITLE INTRO GAMEPLAY FADE IN', 'v23_bc_gameplay_fade_in'])
        g.hat('event_whenbroadcastreceived', [g.call('HUD | present camera status')],
              ['ENGINE FRAME RENDERED', 'porter_frame_rendered'])

    report = dict(version=1,
        damage_blink='Skip Mario and his shadow when InvincTimer >= 3 and the 30 Hz simulation tick is odd; animation and collision continue',
        camera_hud='ROM camera and mode glyphs at native (266,205), scaled to 480x360; zoom and C-Up arrows follow accepted camera state',
        camera_modes='Lakitu by default; Fixed for cinematic/lobby camera; Mario artwork available for an explicit MARIO camera mode',
        frame_policy='One render-completion update; no forever loop or additional scene rendering')
    meta['damage_camera_hud_v14'] = report
    return report
