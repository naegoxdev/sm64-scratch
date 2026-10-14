"""Assetless beta fixes and native HUD placement.

Artwork remains ROM-decoded. Changes are applied after the previous patches,
both in the ordinary builder and when migrating another gameplay source.
"""
from copy import deepcopy

from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition, _replace_body
from sm64_scratch.patches.patch_game_features import _sprite

POWER_METER_X = -48


def apply_beta010(project):
    from sm64_scratch.patches.patch_logo_startup import apply_logo_startup
    apply_logo_startup(project)
    from sm64_scratch.patches.patch_session_death_camera import apply_session_death_camera
    apply_session_death_camera(project)
    meta = project.setdefault('extensionData', {})
    _native_bob_entry(project)
    _hide_hud_on_title(project)
    _position_power_meter(project)
    render = next(t for t in project['targets'] if t['name'] == 'Render')
    mario = next(t for t in project['targets'] if t['name'] == 'Mario')
    if (meta.get('beta010_v16') and 'beta010_v16_complete' in render['blocks']
            and any(b.get('mutation',{}).get('proccode') == 'CAMERA | reset after BOB warp'
                    for b in mario['blocks'].values())):
        meta['beta010_v16'].update(version=2,
            camera='Native init_camera offset: four units behind Mario, 1.25 above the spawn floor; clear lobby tail and seed logical goals before reveal',
            power_meter_x=POWER_METER_X)
        from sm64_scratch.patches.patch_compact_hud import apply_compact_hud
        apply_compact_hud(project)
        return meta['beta010_v16']
    stage = next(t for t in project['targets'] if t.get('isStage'))
    targets = {t['name']: t for t in project['targets']}
    rg = Graph(targets['Render'], stage)

    # A fully offscreen triangle must be skipped, not terminate the render
    # worker. Scratch's "stop this script" exits all its nested procedures.
    guard = rg.blocks['cP']
    remaining = guard['next']
    condition = rg.op('not', guard['inputs']['CONDITION'][1])
    guard['inputs'] = {'CONDITION': [2, condition], 'SUBSTACK': [2, remaining]}
    rg.blocks[condition]['parent'] = 'cP'
    rg.blocks[remaining]['parent'] = 'cP'
    guard['next'] = None
    rg.blocks.pop('ad)')

    # Publish completion after the pen image has been uploaded. Broadcasting
    # before rendering let a forced warp yield advance wipes over an old image.
    done = rg.node('event_broadcast')
    rg.blocks['beta010_v16_complete'] = rg.blocks.pop(done)
    done = 'beta010_v16_complete'
    rg.blocks[done]['inputs'] = {'BROADCAST_INPUT':
        [1, [11, 'ENGINE FRAME RENDERED', 'porter_frame_rendered']]}
    _replace_body(rg, 'FRAME | update render', [
        rg.control('control_if', rg.op('equals', rg.var('TitleIntro.Active'), 0),
                   [rg.call('RENDER | draw scene or reuse wipe image')]), done])

    blink = rg.var('Render.DamageBlink', 0)
    setter = next(b for b in rg.blocks.values() if b['opcode'] == 'data_setvariableto'
                  and b.get('fields', {}).get('VARIABLE', [''])[0] == blink[1])
    predicate = rg.op('and', rg.op('gt', rg.var('SM64.InvincTimer'), 2),
        rg.op('and', rg.op('equals', rg.op('mod', rg.var('SM64.SimulationFrame'), 2), 1),
              rg.op('equals', blink, 0)))
    setter['inputs']['VALUE'] = [3, predicate, [10, '']]
    rg.blocks[predicate]['parent'] = next(k for k,b in rg.blocks.items() if b is setter)
    rg.hat('event_whenflagclicked', [rg.set(blink, 0)])

    wg = Graph(targets['World + Assets'], stage)
    # A lobby door's four-frame tail otherwise overrides the new level's
    # snapped camera, aiming at coordinates from the previous room.
    wg.insert_after('L@', [
        wg.set(wg.var('CamCastle.DoorTail'), 0),
        wg.set(wg.var('RoomDoor.CameraFollowStarted'), 0),
        wg.set(wg.var('Chomp.CutsceneActive'), 0),
        wg.set(wg.var('CamSM64.Init'), 0),
        wg.set(wg.var('CamSM64.SnapLakitu'), 1)])
    # Initialize every logical camera goal from the snapped new pose before
    # the new scene is revealed, rather than carrying a lobby interpolation.
    mg = Graph(targets['Mario'], stage)
    mg.procedure('CAMERA | reset after BOB warp', [
        mg.control('control_if', mg.op('and', mg.op('equals', mg.var('Level.ID'), 'BOB'),
            mg.op('equals', mg.var('Camera64.JustInherited'), 1)), [
            mg.call('SM64 CAMERA DECOMP2 | init BOB camera'),
            mg.set(mg.var('CamSM64.SnapLakitu'), 1),
            mg.set(mg.var('Camera64.JustInherited'), 0)])])
    definition = _definition(mg, 'FRAME | update camera')
    mg.insert_after(definition, [mg.call('CAMERA | reset after BOB warp')])

    tg = Graph(targets['Transitions'], stage)
    phase = tg.var('Painting.TransitionPhase')
    prepared = tg.var('Painting.ScenePresented')
    start = tg.var('Painting.RevealStart', -1)
    elapsed = tg.var('Painting.StarRevealElapsed')
    step = tg.var('Painting.StarRevealStep')
    frame = tg.var('Painting.StarRevealFrame')
    # Retain wall-clock speed at ordinary FPS, but never skip more than two
    # mask frames after a slow software render or a long level load.
    reveal = [
        tg.control('control_if', tg.op('equals', prepared, 0), [
            tg.set(prepared, 1), tg.set(start, tg.node('sensing_timer')),
            tg.set(elapsed, 0)]),
        tg.set(step, tg.op('subtract', tg.node('sensing_timer'), start)),
        tg.control('control_if', tg.op('gt', step, 2/30), [tg.set(step, 2/30)]),
        tg.control('control_if', tg.op('lt', step, 0), [tg.set(step, 0)]),
        tg.set(elapsed, step, True), tg.set(start, tg.node('sensing_timer')),
        tg.set(frame, tg.math('floor', tg.op('add', tg.op('multiply', elapsed, 30), .000001))),
        tg.control('control_if_else', tg.op('lt', frame, 16), [
            tg.node('looks_cleargraphiceffects'),
            tg.node('looks_switchcostumeto', {'COSTUME': tg.op('add', frame, 2)}),
            tg.node('looks_show')], [tg.node('looks_hide'), tg.set(phase, 0),
            tg.set(tg.var('Painting.Fade'), 0), tg.set(prepared, 0)])]
    _replace_body(tg, 'WIPE | present painting star', [
        tg.control('control_if_else', tg.op('equals', phase, 0), [
            tg.node('looks_hide'), tg.set(prepared, 0), tg.set(start, -1)], [
            tg.node('motion_gotoxy', {'X':0, 'Y':0}),
            tg.node('looks_setsizeto', {'SIZE':100}),
            tg.node('looks_gotofrontback', fields={'FRONT_BACK':['front',None]}),
            tg.control('control_if_else', tg.op('equals', phase, 1), [
                tg.set(prepared, 0), tg.node('looks_cleargraphiceffects'),
                tg.node('looks_switchcostumeto', {'COSTUME':1}),
                tg.node('looks_seteffectto',
                    {'VALUE':tg.op('subtract', 100, tg.var('Painting.Fade'))},
                    {'EFFECT':['GHOST',None]}), tg.node('looks_show')], reveal)])])

    _native_hud(project, stage)
    _hide_hud_on_title(project)
    from sm64_scratch.patches.patch_compact_hud import apply_compact_hud
    apply_compact_hud(project)
    report = dict(version=2, beta='0.1.0', power_meter_x=POWER_METER_X,
        painting='Native white star mask; completion after drawing; bounded mask steps at low FPS',
        camera='Native init_camera offset: four units behind Mario, 1.25 above the spawn floor; clear lobby tail and seed logical goals before reveal',
        damage='Native invulnerability predicate; no consecutive hidden display frames; low health alone never hides Mario',
        doors='Drop opaque duplicate decal triangles; retain ROM color-key transparency',
        hud='Native glyph positions/12-pixel advance and uncropped 64x64 meter at 1.5x; update once per completed frame',
        verification='Source and packaged graph inspection; no runtime/gameplay test')
    meta['beta010_v16'] = report
    return report


def _native_bob_entry(project):
    """Translate camera.c:init_camera's (0,125,400) rotated offset.

    offset_rotated flips Z, so the existing FaceYaw+180 is intentional.
    The ROM collision surface under the supported (-6558,6464) spawn is Y=0.
    Native focus starts at Mario's actual airborne position, not 135 above it.
    """
    stage = next(t for t in project['targets'] if t.get('isStage'))
    world = next(t for t in project['targets'] if t['name']=='World + Assets')
    g = Graph(world,stage)
    if world['blocks']['Jj']['inputs']['VALUE'] == [1,[4,4]]:
        return
    for identity,value in (('Jj',4),('Jk',4),('k-',1.25),('Jm',0)):
        world['blocks'][identity]['inputs']['VALUE'] = [1,[4,value]]
    for identity in ('k,','k.'):
        root = world['blocks'][identity]['inputs']['VALUE'][1]
        multiplier = world['blocks'][root]['inputs']['NUM2'][1]
        world['blocks'][multiplier]['inputs']['NUM2'] = [1,[4,4]]
    def value(identity, reporter):
        world['blocks'][identity]['inputs']['VALUE'] = [3,reporter,[10,'']]
        world['blocks'][reporter]['parent'] = identity
    value('Jl',g.op('subtract',1.25,g.var('SM64.PosY')))
    value('k/',g.node('operator_add',{'NUM1':g.var('SM64.PosY'),'NUM2':0}))
    value('JB',g.math('atan',g.op('divide',g.op('subtract',
          g.var('Camera64.FocusY'),g.var('cam y')),4)))


def _native_hud(project, stage):
    ready = Graph(stage, stage).var('HUD.NativeReady', 0)
    lives = Graph(stage, stage).var('HUD.Lives', 4)
    targets = {t['name']: t for t in project['targets']}
    layer = targets['SM64 HUD']['layerOrder']
    for name in ('SM64 HUD', 'SM64 HUD Coins', 'SM64 HUD Stars',
                 'SM64 HUD Lives', 'SM64 Power Meter'):
        target, _, g = _sprite(project, name)
        placeholder = deepcopy(target['costumes'][0])
        if name == 'SM64 HUD':
            names = ('HUD icons', 'HUD icons no star multiplier')
            center = (240,180)
        elif name == 'SM64 Power Meter':
            names = tuple(map(str, range(9))); center = (48,48)
        else:
            names = tuple(map(str, range(1000 if name != 'SM64 HUD Lives' else 101)))
            center = (0,12)
        target['costumes'] = [dict(placeholder, name=n, bitmapResolution=1,
            rotationCenterX=center[0], rotationCenterY=center[1]) for n in names]
        target.update(x=0,y=0,size=100,visible=False,layerOrder=layer,
                      rotationStyle="don't rotate")
        valid = g.op('and', g.op('equals', ready, 1),
                     g.op('equals', g.var('TitleIntro.Active'), 0))
        if name == 'SM64 HUD':
            costume = g.var('HUD.StripCostume', local=True)
            body = [g.control('control_if_else', g.op('lt', g.var('Star.Count'), 100),
                [g.set(costume,'HUD icons')], [g.set(costume,'HUD icons no star multiplier')]),
                g.node('looks_switchcostumeto', {'COSTUME':costume}),
                g.node('motion_gotoxy', {'X':0,'Y':0}),g.node('looks_show')]
        elif name == 'SM64 Power Meter':
            valid = g.op('and', valid, g.op('not',g.op('equals',g.var('SM64.PowerMeterState'),'HIDDEN')))
            body = [g.node('motion_gotoxy', {'X':POWER_METER_X,'Y':g.op('subtract',
                g.op('multiply',g.var('SM64.PowerMeterY'),1.5),180)}),
                g.node('looks_switchcostumeto', {'COSTUME':g.node('operator_join',
                    {'STRING1':'','STRING2':g.var('SM64.HealthWedges')})}),g.node('looks_show')]
        else:
            value = g.var({'SM64 HUD Coins':'Coin.Count','SM64 HUD Stars':'Star.Count',
                           'SM64 HUD Lives':'HUD.Lives'}[name])
            x = 57 if name == 'SM64 HUD Coins' else -159 if name == 'SM64 HUD Lives' else 168
            if name == 'SM64 HUD Stars':
                x = g.op('subtract',168,g.op('multiply',21,g.op('not',g.op('lt',value,100))))
            body = [g.node('motion_gotoxy',{'X':x,'Y':145.5}),
                    g.node('looks_switchcostumeto',{'COSTUME':g.node('operator_join',
                        {'STRING1':'','STRING2':value})}),g.node('looks_show')]
        g.procedure('HUD | present native display', [g.control('control_if_else',valid,
                    [g.node('looks_cleargraphiceffects'),*body],[g.node('looks_hide')])])
        g.hat('event_whenflagclicked',[g.node('looks_hide')])
        g.hat('event_whenbroadcastreceived',[g.call('HUD | present native display')],
              ['ENGINE FRAME RENDERED','porter_frame_rendered'])
    g = Graph(targets['Mario'], stage)
    g.hat('event_whenflagclicked',[g.set(ready,0),g.set(lives,4)])
    g.hat('event_whenbroadcastreceived',[g.set(ready,0)],['TITLE INTRO RUN','titleintro_bc_run'])
    g.hat('event_whenbroadcastreceived',[g.set(ready,1)],
          ['TITLE INTRO GAMEPLAY FADE IN','v23_bc_gameplay_fade_in'])
    g.hat('event_whenbroadcastreceived',[
        g.control('control_if',g.op('gt',lives,0),[g.set(lives,-1,True)])],
        ['DEATH BOWSER WIPE','gameplay_v13_death_bowser_wipe'])


def _position_power_meter(project):
    """Keep the meter 18 stage pixels left of its native HUD position."""
    for target in project['targets']:
        if target['name'] != 'SM64 Power Meter':
            continue
        target['x'] = POWER_METER_X
        for block in target['blocks'].values():
            if block['opcode'] == 'motion_gotoxy':
                block['inputs']['X'] = [1, [4, POWER_METER_X]]


def _hide_hud_on_title(project):
    stage = next(t for t in project['targets'] if t.get('isStage'))
    for target in project['targets']:
        if target['name'] not in ('SM64 HUD','SM64 HUD Coins','SM64 HUD Stars',
                                 'SM64 HUD Lives','SM64 Power Meter'):
            continue
        if any(b['opcode']=='event_whenbroadcastreceived' and
               b.get('fields',{}).get('BROADCAST_OPTION',[''])[0]=='TITLE INTRO RUN'
               for b in target['blocks'].values()):
            continue
        g = Graph(target,stage)
        g.hat('event_whenbroadcastreceived',[g.node('looks_hide')],
              ['TITLE INTRO RUN','titleintro_bc_run'])
