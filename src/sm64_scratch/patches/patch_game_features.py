"""Reapply porter-owned credits and audio observers without replacing game logic."""
from copy import deepcopy
from html import escape
from io import BytesIO
from pathlib import Path
import hashlib,json,zipfile
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.sb3_builder import _write_project
from sm64_scratch.decoders.decode_game_audio import GAME_AUDIO_DISPLAY_NAMES, terrain_sound_table, grounds_bird_emitters
from sm64_scratch.decoders.decode_audio import TITLE_AUDIO_DISPLAY_NAMES

PORT_CREDIT = 'Port by Naegox (Pablo)'
BETA_VERSION = '0.1.0'
NOTICE_NAME = 'Project Credit Notice'
AUDIO_NAME = 'Gameplay Audio'
MUSIC_NAME = 'Gameplay Music'
INTRO_MUSIC_NAME = 'Intro Cutscene Music'
TREE_AUDIO_NAME = 'Tree Movement Audio'
BIRD_AUDIO_NAMES = tuple('Grounds Bird Audio '+str(i) for i in range(1,4))
GOOMBA_AUDIO_NAME = 'Goomba Audio'
PORTER_OWNED_SPRITES = (NOTICE_NAME,AUDIO_NAME,MUSIC_NAME,INTRO_MUSIC_NAME,TREE_AUDIO_NAME,GOOMBA_AUDIO_NAME,'Camera Audio','Gameplay Music Player','Recovery Audio')+BIRD_AUDIO_NAMES


def _add_intro_music(project, sound_records):
    target, stage, g = _sprite(project, INTRO_MUSIC_NAME)
    name = GAME_AUDIO_DISPLAY_NAMES['game_intro_cutscene_music']
    target['sounds'] = [deepcopy(sound_records[name])]
    target['volume'] = 0
    armed = g.var('Audio.IntroMusicArmed', 0, local=True)
    pending = g.var('Audio.IntroMusicPending', 0, local=True)
    g.hat('event_whenbroadcastreceived', [g.set(pending, 1)],
          ['TITLE INTRO GAMEPLAY FADE IN', 'v23_bc_gameplay_fade_in'])
    reset = lambda: [g.set(armed, 0), g.set(pending, 0), g.node('sound_setvolumeto', {'VOLUME':0})]
    g.hat('event_whenbroadcastreceived', reset(), ['TITLE INTRO RUN', 'titleintro_bc_run'])
    valid = lambda: g.op('and', g.op('equals', g.var('TitleIntro.Active'), 0), g.op('equals', g.var('Level.ID'), 'GROUNDS'))
    start = g.op('and', g.op('equals', pending, 1), g.op('and', valid(), g.op('equals', g.var('Intro.Active'), 1)))
    g.hat('event_whenflagclicked', reset() + [g.control('control_forever', None, [
        g.control('control_if', start, [g.set(pending, 0), g.set(armed, 1), g.node('sound_setvolumeto', {'VOLUME':100}), _sound(g, name)]),
        g.control('control_if', g.op('not', valid()), [g.set(armed, 0), g.node('sound_setvolumeto', {'VOLUME':0})]),
        g.node('control_wait', {'DURATION':1/30})])])
    return target


def _add_level_music(project, sound_records):
    # Only the playback target is stopped. Scratch's global stop-all-sounds
    # also killed the independent arrival score immediately after it started.
    controller, stage, g = _sprite(project, MUSIC_NAME)
    last = g.var('Audio.MusicLevel', '', local=True); ready = g.var('Audio.GameplayReady', 0, local=True)
    label, identity = 'GAMEPLAY MUSIC LEVEL', 'game_audio_bc_music'
    notify = lambda: _broadcast(g, identity, label)
    g.hat('event_whenbroadcastreceived', [g.set(ready, 1)], ['TITLE INTRO GAMEPLAY FADE IN', 'v23_bc_gameplay_fade_in'])
    g.hat('event_whenbroadcastreceived', [g.set(ready, 0), g.set(last, ''), notify()], ['TITLE INTRO RUN', 'titleintro_bc_run'])
    changed = g.op('not', g.op('equals', g.var('Level.ID'), last))
    valid = g.op('and', g.op('equals', ready, 1), g.op('and', g.op('equals', g.var('TitleIntro.Active'), 0), changed))
    g.hat('event_whenflagclicked', [g.set(ready, 0), g.set(last, ''), g.control('control_forever', None, [
        g.control('control_if', valid, [g.set(last, g.var('Level.ID')), notify()]), g.node('control_wait', {'DURATION':1/30})])])
    player, stage, g = _sprite(project, 'Gameplay Music Player')
    player['sounds'] = [deepcopy(sound_records[GAME_AUDIO_DISPLAY_NAMES['game_music_'+level+'_'+part]]) for level in ('bob','lobby') for part in ('intro','loop')]
    # stop other scripts emits STOP_FOR_TARGET, stopping sounds that were
    # started with play-until-done in this target without touching any SFX.
    stop = g.node('control_stop', fields={'STOP_OPTION':['other scripts in sprite',None]}, mutation={'tagName':'mutation','children':[],'hasnext':'true'})
    start_volume = _broadcast(g, 'game_audio_player_volume', 'GAMEPLAY MUSIC PLAYER VOLUME')
    body = [stop, start_volume]
    for level, key in [('BOB','bob'),('LOBBY','lobby')]:
        valid = g.op('and', g.op('equals', g.var('TitleIntro.Active'), 0), g.op('equals', g.var('Level.ID'), level))
        playback = [_sound(g, GAME_AUDIO_DISPLAY_NAMES['game_music_'+key+'_intro'], True),
                    g.control('control_repeat_until', g.op('or', g.op('equals', g.var('TitleIntro.Active'), 1), g.op('not', g.op('equals', g.var('Level.ID'), level))),
                    [_sound(g, GAME_AUDIO_DISPLAY_NAMES['game_music_'+key+'_loop'], True)])]
        body.append(g.control('control_if', valid, playback))
    g.hat('event_whenbroadcastreceived', body, [label, identity])
    mute = g.op('or', g.op('equals', g.var('TitleIntro.Active'), 1), g.op('equals', g.var('Audio.StarCelebrating'), 1))
    g.hat('event_whenbroadcastreceived', [g.control('control_forever', None, [
        g.control('control_if_else', mute, [g.node('sound_setvolumeto', {'VOLUME':0})], [g.node('sound_setvolumeto', {'VOLUME':100})]),
        g.node('control_wait', {'DURATION':1/30})])], ['GAMEPLAY MUSIC PLAYER VOLUME','game_audio_player_volume'])
    return controller, player


def _add_recovery_audio(project, sound_records):
    target, stage, g = _sprite(project, 'Recovery Audio')
    cues = {'DEATH BOWSER WIPE':'game_bowser_death', 'COIN SPURT':'game_coin_spurt', 'COIN BOUNCE':'game_coin_bounce'}
    target['sounds'] = [deepcopy(sound_records[GAME_AUDIO_DISPLAY_NAMES[key]]) for key in cues.values()]
    for label, key in cues.items():
        identity = 'gameplay_v13_' + label.lower().replace(' ', '_')
        g.hat('event_whenbroadcastreceived', [_sound(g, GAME_AUDIO_DISPLAY_NAMES[key])], [label, identity])
    return target


def _local_list(g,name):
    identity='game_audio_local_'+g.target['name'].replace(' ','_')+'_'+name.replace('.','_')
    g.target['lists'][identity]=[name,[]]
    return [name,identity]


def _add_goomba_audio(project,sound_records):
    target,stage,g=_sprite(project,GOOMBA_AUDIO_NAME)
    list_names={row[0] for row in stage['lists'].values()}
    missing={'BOB.Goomba.'+key for key in ('State','Action','Anim','RelativeSpeed','X','Z')} - list_names
    if missing:raise ValueError('Goomba audio needs preserved actor lists: '+', '.join(sorted(missing)))
    target['sounds']=[deepcopy(sound_records[GAME_AUDIO_DISPLAY_NAMES['game_goomba_'+k]]) for k in ('step','alert','stomp','death')]
    index=g.var('Audio.GoombaIndex',1,local=True)
    ready=g.var('Audio.GameplayReady',0,local=True)
    previous_level=g.var('Audio.GoombaPreviousLevel','',local=True)
    caches={key:_local_list(g,'Audio.PreviousGoomba.'+key) for key in ('State','Action','Anim')}
    now=lambda key:g.item('BOB.Goomba.'+key,index)
    old=lambda key:g.node('data_itemoflist',{'INDEX':index},{'LIST':caches[key]})
    def play(key):return _sound(g,GAME_AUDIO_DISPLAY_NAMES['game_goomba_'+key])
    distance=g.var('Audio.GoombaDistanceSquared',0,local=True)
    delta=lambda axis:g.op('subtract',g.var('SM64.Pos'+axis),now(axis))
    # BOB only contains normal-size Goombas, using the native high death cue.
    frames=[]
    for frame in (2,17):
        crossed=g.op('and',g.op('not',g.op('lt',now('Anim'),frame)),g.op('or',g.op('lt',old('Anim'),frame),g.op('lt',now('Anim'),old('Anim'))))
        frames.append(g.control('control_if',crossed,[play('step')]))
    events=[g.control('control_if',g.op('and',g.op('equals',now('State'),0),
                g.op('and',g.op('equals',now('Action'),1),g.op('not',g.op('equals',old('Action'),1)))),[play('alert')]),
            g.control('control_if',g.op('and',g.op('equals',now('State'),1),g.op('equals',old('State'),0)),[play('stomp')]),
            g.control('control_if',g.op('and',g.op('equals',now('State'),3),g.op('or',g.op('equals',old('State'),1),g.op('equals',old('State'),2))),[play('death')]),
            g.control('control_if',g.op('and',g.op('equals',now('State'),0),g.op('and',g.op('equals',now('Action'),0),g.op('gt',now('RelativeSpeed'),4/3))),frames)]
    body=[g.set(distance,g.op('add',g.op('multiply',delta('X'),delta('X')),g.op('multiply',delta('Z'),delta('Z')))),
          g.control('control_if',g.op('and',g.op('equals',ready,1),g.op('and',g.op('equals',g.var('TitleIntro.Active'),0),
                    g.op('and',g.op('equals',g.var('Level.ID'),previous_level),g.op('and',g.op('not',g.op('gt',index,g.node('data_lengthoflist',fields={'LIST':caches['State']}))),
                    g.op('and',g.op('equals',g.var('Level.ID'),'BOB'),g.op('lt',distance,1600)))))),events)]
    for key in caches:
        body.append(g.control('control_if_else',g.op('gt',g.length('BOB.Goomba.State'),g.node('data_lengthoflist',fields={'LIST':caches[key]})),
                    [g.node('data_addtolist',{'ITEM':now(key)},{'LIST':caches[key]})],
                    [g.node('data_replaceitemoflist',{'INDEX':index,'ITEM':now(key)},{'LIST':caches[key]})]))
    body.append(g.set(index,1,True))
    g.procedure('AUDIO | observe Goomba states',[g.set(index,1),g.control('control_repeat',g.length('BOB.Goomba.State'),body),g.set(previous_level,g.var('Level.ID'))])
    reset=[g.node('data_deletealloflist',fields={'LIST':row}) for row in caches.values()]
    # Take the initial snapshot while gated, so saved actor states cannot sound.
    g.hat('event_whenflagclicked',[g.set(ready,0),g.set(previous_level,'')]+reset+[g.call('AUDIO | observe Goomba states'),g.control('control_forever',None,[g.call('AUDIO | observe Goomba states'),g.node('control_wait',{'DURATION':1/30})])])
    g.hat('event_whenbroadcastreceived',[g.set(ready,0)],['TITLE INTRO RUN','titleintro_bc_run'])
    g.hat('event_whenbroadcastreceived',[g.set(ready,1)],['TITLE INTRO GAMEPLAY FADE IN','v23_bc_gameplay_fade_in'])
    return target


def _add_grounds_birds(project,rom,sound_records):
    sprites=[]
    for number,(name,position) in enumerate(zip(BIRD_AUDIO_NAMES,grounds_bird_emitters(rom)),1):
        target,stage,g=_sprite(project,name)
        target['sounds']=[deepcopy(sound_records[GAME_AUDIO_DISPLAY_NAMES['game_birds_'+str(number)]])]
        target['volume']=0
        ready=g.var('Audio.GameplayReady',0,local=True)
        active=g.var('Audio.GroundsBirdActive',0,local=True)
        distance=g.var('Audio.BirdDistance',0,local=True)
        intensity=g.var('Audio.BirdIntensity',0,local=True)
        terms=[g.op('multiply',g.op('subtract',g.var('cam '+axis),value),g.op('subtract',g.var('cam '+axis),value)) for axis,value in zip('xyz',position)]
        valid=g.op('and',g.op('equals',ready,1),g.op('and',g.op('equals',g.var('TitleIntro.Active'),0),g.op('equals',g.var('Level.ID'),'GROUNDS')))
        volume=[g.set(distance,g.math('sqrt',g.op('add',terms[0],g.op('add',terms[1],terms[2])))),
                g.control('control_if_else',g.op('lt',distance,250/3),
                   [g.set(intensity,g.op('subtract',1,g.op('multiply',g.op('divide',distance,250/3),.8)))],
                   [g.set(intensity,g.op('multiply',g.op('divide',g.op('subtract',220,distance),220-250/3),.2))]),
                g.control('control_if',g.op('gt',distance,220),[g.set(intensity,0)]),
                g.node('sound_setvolumeto',{'VOLUME':g.op('multiply',100,g.op('add',g.op('multiply',.8,g.op('multiply',intensity,intensity)),.2))})]
        g.hat('event_whenflagclicked',[g.set(ready,0),g.set(active,0),g.node('sound_setvolumeto',{'VOLUME':0}),
              g.control('control_forever',None,[g.set(active,valid),g.control('control_if_else',g.op('equals',active,1),volume,[g.node('sound_setvolumeto',{'VOLUME':0})]),g.node('control_wait',{'DURATION':1/30})])])
        g.hat('event_whenflagclicked',[g.control('control_forever',None,[g.control('control_if_else',g.op('equals',active,1),
              [_sound(g,target['sounds'][0]['name'],True)],[g.node('control_wait',{'DURATION':1/30})])])])
        g.hat('event_whenbroadcastreceived',[g.set(ready,0),g.set(active,0),g.node('sound_setvolumeto',{'VOLUME':0})],['TITLE INTRO RUN','titleintro_bc_run'])
        g.hat('event_whenbroadcastreceived',[g.set(ready,1)],['TITLE INTRO GAMEPLAY FADE IN','v23_bc_gameplay_fade_in'])
        sprites.append(target)
    return sprites


def _sprite(project, name):
    stage = next(t for t in project['targets'] if t.get('isStage'))
    old = next((t for t in project['targets'] if t['name'] == name), None)
    if old is not None:
        project['targets'].remove(old)
    sprite = dict(isStage=False, name=name, variables={}, lists={}, broadcasts={},
                  blocks={}, comments={}, currentCostume=0, costumes=[deepcopy(stage['costumes'][0])],
                  sounds=[], volume=100, layerOrder=106, visible=False, x=0, y=0,
                  size=100, direction=90, draggable=False, rotationStyle='all around')
    project['targets'].append(sprite)
    return sprite, stage, Graph(sprite, stage)


def _sound(g, name, wait=False):
    menu = g.node('sound_sounds_menu', fields={'SOUND_MENU': [name, None]})
    g.blocks[menu]['shadow'] = True
    block = g.node('sound_playuntildone' if wait else 'sound_play')
    g.blocks[block]['inputs']['SOUND_MENU'] = [1, menu]
    g.blocks[menu]['parent'] = block
    return block


def _broadcast(g, identity, name):
    g.stage['broadcasts'][identity] = name
    block = g.node('event_broadcast')
    g.blocks[block]['inputs']['BROADCAST_INPUT'] = [1, [11, name, identity]]
    return block


def add_project_credit(project, credit=PORT_CREDIT):
    target, stage, g = _sprite(project, NOTICE_NAME)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="470" height="28" viewBox="0 0 470 28">'
           f'<g font-family="Arial, sans-serif" text-anchor="middle" fill="white">'
           f'<text x="235" y="11" font-size="10">{escape(credit)} · Beta {BETA_VERSION}</text>'
           f'<text x="235" y="25" font-size="9">This fan project is not affiliated with or endorsed by Nintendo.</text>'
           f'</g></svg>').encode()
    digest = hashlib.md5(svg).hexdigest()
    target['costumes'] = [dict(name='Project notice', assetId=digest, md5ext=digest+'.svg',
                               dataFormat='svg', bitmapResolution=1,
                               rotationCenterX=235, rotationCenterY=14)]
    target['y'] = -150
    g.hat('event_whenflagclicked', [g.node('looks_hide')])
    g.hat('event_whenbroadcastreceived', [g.node('looks_cleargraphiceffects'),
          g.node('looks_show')], ['TITLE INTRO LOGO', 'titleintro_bc_logo'])
    g.hat('event_whenbroadcastreceived', [g.node('looks_hide')],
          ['TITLE INTRO FADE OUT', 'titleintro_bc_fadeout'])
    for name, identity in [('TITLE INTRO HEAD','titleintro_bc_head'), ('TITLE INTRO END','titleintro_bc_end')]:
        g.hat('event_whenbroadcastreceived', [g.node('looks_hide')], [name, identity])
    return {digest+'.svg': svg}


def add_game_audio_observers(project, rom):
    # Retain media when regenerating an already populated local build.
    sound_records = {s['name']:deepcopy(s) for t in project['targets']
                     for s in t.get('sounds', [])}
    target, stage, g = _sprite(project, AUDIO_NAME)
    names = {row[0] for row in stage['variables'].values()}
    required = {'TitleIntro.Active','Level.ID','SM64.Action','SM64.Grounded','SM64.Health',
                'SM64.HurtCounter','Coin.Count','Star.Count','Door.State','RoomDoor.State',
                'SM64.FloorType','SM64.FloorHeight','SM64.WaterLevel','Anim.Clip','Anim.LocalFrame',
                'SM64.PunchPhase','SM64.ActionArg','Tree.Climb.PolePos','Painting.State','Star.State','SM64.ActionTimer','SM64.ActionState',
                'Chomp.Active','Chomp.State','Chomp.SubAction','Chomp.Airborne','Chomp.Speed',
                'Chomp.VelY','Chomp.ReleaseBounces','Chomp.PostHits','Chomp.GateBroken'}
    required.update({'Intro.Active','Intro.Phase','SM64.PosX','SM64.PosY','SM64.PosZ','SM64.InputAnalog','cam x','cam y','cam z'})
    required.add('SM64.GroundPoundPhase')
    missing = sorted(required - names)
    if missing:
        raise ValueError('Gameplay audio needs preserved state variables: ' + ', '.join(missing))
    target['sounds'] = [deepcopy(s) for s in sound_records.values()
                       if s['name'] in set(GAME_AUDIO_DISPLAY_NAMES.values()) | {TITLE_AUDIO_DISPLAY_NAMES['coin']}]
    separate_names={GAME_AUDIO_DISPLAY_NAMES[key] for key in GAME_AUDIO_DISPLAY_NAMES if key.startswith(('game_goomba_','game_birds_')) or key in ('game_coin_spurt','game_coin_bounce','game_bowser_death')}
    target['sounds'] = [s for s in target['sounds'] if ' Music ' not in s['name'] and s['name'] != GAME_AUDIO_DISPLAY_NAMES['game_tree_slide'] and s['name'] not in separate_names]
    fields = ['Level.ID','SM64.Action','SM64.Grounded','SM64.Health','SM64.HurtCounter',
              'Coin.Count','Star.Count','Door.State','RoomDoor.State','Anim.Clip','Anim.LocalFrame',
              'SM64.PunchPhase','SM64.ActionArg','Tree.Climb.PolePos','Painting.State','Star.State','SM64.ActionTimer','SM64.ActionState',
              'Chomp.State','Chomp.SubAction','Chomp.Airborne','Chomp.ReleaseBounces','Chomp.PostHits','Chomp.GateBroken','Intro.Active','Intro.Phase']
    fields.extend(['SM64.GroundPoundPhase', 'SM64.HealthWedges', 'BOB.RedCoin.Count'])
    previous = {name:g.var('Audio.Previous.'+name, local=True) for name in fields}
    current = lambda name:g.var(name)
    terrain = g.var('Audio.Terrain', local=True)
    surface_class = g.var('Audio.SurfaceClass', local=True)
    terrain_rows = terrain_sound_table(rom)
    # The current levels use TERRAIN_GRASS (grounds/BOB) and TERRAIN_STONE (lobby).
    classification = [g.set(surface_class, 0)]
    for index, types in [(1,['SURFACE_NOT_SLIPPERY','SURFACE_HARD','SURFACE_HARD_NOT_SLIPPERY','SURFACE_SWITCH']),
                         (2,['SURFACE_SLIPPERY','SURFACE_HARD_SLIPPERY','SURFACE_NO_CAM_COL_SLIPPERY']),
                         (3,['SURFACE_VERY_SLIPPERY','SURFACE_ICE','SURFACE_HARD_VERY_SLIPPERY','SURFACE_NOISE_VERY_SLIPPERY','SURFACE_NOISE_VERY_SLIPPERY_73','SURFACE_NOISE_VERY_SLIPPERY_74','SURFACE_NO_CAM_COL_VERY_SLIPPERY']),
                         (4,['SURFACE_NOISE_DEFAULT']), (5,['SURFACE_NOISE_SLIPPERY'])]:
        for value in types:
            classification.append(g.control('control_if',g.op('equals',current('SM64.FloorType'),value),[g.set(surface_class,index)]))
    for index in range(6):
        classification.append(g.control('control_if',g.op('equals',surface_class,index),[
            g.control('control_if_else',g.op('equals',current('Level.ID'),'LOBBY'),
                      [g.set(terrain,terrain_rows[1][index])],[g.set(terrain,terrain_rows[0][index])])]))
    classification.append(g.control('control_if',g.op('lt',current('SM64.FloorHeight'),g.op('subtract',current('SM64.WaterLevel'),.1)),[g.set(terrain,2)]))
    for value in ['SURFACE_SHALLOW_QUICKSAND','SURFACE_DEEP_QUICKSAND','SURFACE_INSTANT_QUICKSAND','SURFACE_DEEP_MOVING_QUICKSAND','SURFACE_SHALLOW_MOVING_QUICKSAND','SURFACE_QUICKSAND','SURFACE_MOVING_QUICKSAND']:
        classification.append(g.control('control_if',g.op('equals',current('SM64.FloorType'),value),[g.set(terrain,7)]))
    def play(key): return _sound(g, GAME_AUDIO_DISPLAY_NAMES.get(key,TITLE_AUDIO_DISPLAY_NAMES.get(key)))
    def terrain_sound(action):
        return [g.control('control_if',g.op('equals',terrain,i),[play(f'game_{action}_{i}')]) for i in range(8)]
    events = classification
    # Jump audio follows the US decomp action by action. Normal jump, side flip
    # and wall kick use play_mario_jump_sound(), which randomly chooses Yah/Wah/Hoo.
    # The US triple jump uses its separate 5-entry Yahoo/Waha/Yippee pool.
    jump_voice_choice = g.var('Audio.JumpVoiceChoice', 1, local=True)
    normal_jump_voices = ('game_jump_voice','game_jump_voice_2','game_jump_voice_3')
    triple_jump_voices = tuple(f'game_triple_jump_random_{i}' for i in range(1,6))
    def action_entry(action):
        return g.op('and', g.op('equals', current('SM64.Action'), action),
                    g.op('not', g.op('equals', previous['SM64.Action'], action)))
    def random_voice(keys):
        body=[g.set(jump_voice_choice,g.op('random',1,len(keys)))]
        body.extend(g.control('control_if',g.op('equals',jump_voice_choice,i),[play(key)])
                    for i,key in enumerate(keys,1))
        return body
    events.append(g.control('control_if', action_entry('ACT_JUMP'),
                            terrain_sound('jump') + random_voice(normal_jump_voices)))
    events.append(g.control('control_if', action_entry('ACT_DOUBLE_JUMP'),
                            terrain_sound('jump') + [play('game_double_jump_voice')]))
    events.append(g.control('control_if', action_entry('ACT_TRIPLE_JUMP'),
                            terrain_sound('jump') + random_voice(triple_jump_voices)))
    # Backflip passes SOUND_MARIO_YAH_WAH_HOO directly, so unlike the helper it
    # plays the base Yah entry rather than randomly selecting one of the three.
    events.append(g.control('control_if', action_entry('ACT_BACKFLIP'),
                            terrain_sound('jump') + [play('game_jump_voice')]))
    events.append(g.control('control_if', action_entry('ACT_SIDE_FLIP'),
                            terrain_sound('jump') + random_voice(normal_jump_voices)))
    events.append(g.control('control_if', action_entry('ACT_LONG_JUMP'),
                            terrain_sound('jump') + [play('game_triple_jump_voice')]))
    # Wall kicks call play_mario_jump_sound() directly: voice only, no terrain
    # jump impact is emitted for ACT_WALL_KICK_AIR itself.
    events.append(g.control('control_if', action_entry('ACT_WALL_KICK_AIR'),
                            random_voice(normal_jump_voices)))
    pound=lambda:g.op('equals',current('SM64.Action'),'ACT_GROUND_POUND')
    pound_land=lambda:g.op('equals',current('SM64.Action'),'ACT_GROUND_POUND_LAND')
    ordinary_landing=g.op('and',g.op('equals',current('SM64.Grounded'),1),g.op('equals',previous['SM64.Grounded'],0))
    ordinary_landing=g.op('and',ordinary_landing,g.op('not',g.op('or',pound_land(),g.op('equals',previous['SM64.Action'],'ACT_GROUND_POUND'))))
    # Fast ledge climbing already emits its native landing at animation frame
    # 8. Becoming grounded at the end is not another physical landing.
    ordinary_landing=g.op('and',ordinary_landing,
        g.op('not',g.op('equals',previous['SM64.Action'],'ACT_LEDGE_CLIMB_FAST')))
    events.append(g.control('control_if',ordinary_landing,terrain_sound('land')))

    # Native ledge audio from mario_actions_automatic.c.  The Scratch
    # animation local frame is one-based, so C animFrame 8 is local frame 9.
    ledge_grab_entry = g.op('and', g.op('equals', current('SM64.Action'), 'ACT_LEDGE_GRAB'),
                            g.op('not', g.op('equals', previous['SM64.Action'], 'ACT_LEDGE_GRAB')))
    events.append(g.control('control_if', ledge_grab_entry, [play('game_ledge_whoa')]))
    slow_ledge = g.op('equals', current('SM64.Action'), 'ACT_LEDGE_CLIMB_SLOW_1')
    slow_voice_crossed = g.op('and', g.op('not', g.op('lt', current('SM64.ActionTimer'), 10)),
                              g.op('or', g.op('not', g.op('equals', previous['SM64.Action'], 'ACT_LEDGE_CLIMB_SLOW_1')),
                                   g.op('lt', previous['SM64.ActionTimer'], 10)))
    events.append(g.control('control_if', g.op('and', slow_ledge, slow_voice_crossed),
                            [play('game_ledge_eeuh')]))
    fast_ledge_entry = g.op('and', g.op('equals', current('SM64.Action'), 'ACT_LEDGE_CLIMB_FAST'),
                            g.op('not', g.op('equals', previous['SM64.Action'], 'ACT_LEDGE_CLIMB_FAST')))
    events.append(g.control('control_if', fast_ledge_entry, [play('game_ledge_uh2')]))
    fast_land_crossed = g.op('and',
        g.op('equals', current('SM64.Action'), 'ACT_LEDGE_CLIMB_FAST'),
        g.op('and', g.op('not', g.op('lt', current('Anim.LocalFrame'), 9)),
             g.op('or', g.op('not', g.op('equals', previous['SM64.Action'], 'ACT_LEDGE_CLIMB_FAST')),
                  g.op('or', g.op('lt', previous['Anim.LocalFrame'], 9),
                       g.op('lt', current('Anim.LocalFrame'), previous['Anim.LocalFrame'])))))
    events.append(g.control('control_if', fast_land_crossed, terrain_sound('land')))

    # Native airborne action: SPIN on entry, WAH when the descent begins,
    # terrain-heavy impact on landing. Each cue has its own state edge.
    events.append(g.control('control_if',g.op('and',pound(),g.op('not',g.op('equals',previous['SM64.Action'],'ACT_GROUND_POUND'))),[play('game_flip_spin')]))
    descent=g.op('and',g.op('equals',current('SM64.GroundPoundPhase'),1),g.op('or',g.op('not',g.op('equals',previous['SM64.Action'],'ACT_GROUND_POUND')),g.op('not',g.op('equals',previous['SM64.GroundPoundPhase'],1))))
    events.append(g.control('control_if',g.op('and',pound(),descent),[play('game_ground_pound_voice')]))
    impact=g.op('and',pound_land(),g.op('not',g.op('equals',previous['SM64.Action'],'ACT_GROUND_POUND_LAND')))
    events.append(g.control('control_if',impact,terrain_sound('heavy_land')))
    # C act_panting uses animation frame 1 and randomly selects three voices.
    panting_frame=g.op('and',g.op('not',g.op('lt',current('Anim.LocalFrame'),2)),g.op('or',g.op('not',g.op('equals',previous['SM64.Action'],'ACT_PANTING')),g.op('or',g.op('lt',previous['Anim.LocalFrame'],2),g.op('lt',current('Anim.LocalFrame'),previous['Anim.LocalFrame']))))
    panting=g.op('and',g.op('equals',current('SM64.Action'),'ACT_PANTING'),g.op('and',g.op('lt',current('SM64.Health'),1280),g.op('gt',current('SM64.Health'),255)))
    panting_voice=g.var('Audio.PantingVoice',1,local=True)
    panting_body=[g.set(panting_voice,g.op('random',1,3))]
    panting_body += [g.control('control_if',g.op('equals',panting_voice,i),[play(f'game_panting_{i}')])for i in range(1,4)]
    events.append(g.control('control_if',g.op('and',panting,panting_frame),panting_body))
    # The authored controller aliases backflip landing to triple-jump landing.
    # Preserve its origin until the native no-analog HAHA can play, once only.
    backflip_land_armed=g.var('Audio.BackflipLandingArmed',0,local=True)
    events.append(g.control('control_if',g.op('equals',current('SM64.Action'),'ACT_BACKFLIP'),[g.set(backflip_land_armed,1)]))
    landing_actions=('ACT_BACKFLIP_LAND','ACT_BACKFLIP_LAND_STOP','ACT_TRIPLE_JUMP_LAND','ACT_TRIPLE_JUMP_LAND_STOP')
    landing_match=g.op('or',g.op('equals',current('SM64.Action'),landing_actions[0]),
                      g.op('or',g.op('equals',current('SM64.Action'),landing_actions[1]),
                           g.op('or',g.op('equals',current('SM64.Action'),landing_actions[2]),g.op('equals',current('SM64.Action'),landing_actions[3]))))
    events.append(g.control('control_if',g.op('and',g.op('equals',backflip_land_armed,1),g.op('not',g.op('equals',current('SM64.Action'),'ACT_BACKFLIP'))),
                  [g.control('control_if_else',g.op('and',g.op('equals',current('SM64.Grounded'),1),landing_match),
                     [g.control('control_if',g.op('equals',current('SM64.InputAnalog'),0),[play('game_backflip_land'),g.set(backflip_land_armed,0)])],
                     [g.set(backflip_land_armed,0)])]))
    # Red pickups supply their own ascending cue; suppress only their two units.
    coin_delta = g.var('Audio.CoinDelta', local=True)
    skipped = current('BOB.RedCoin.AudioSkip')
    events.append(g.control('control_if',g.op('gt',current('Coin.Count'),previous['Coin.Count']),[
        g.set(coin_delta, g.op('subtract', current('Coin.Count'), previous['Coin.Count'])),
        g.control('control_if',g.op('gt',coin_delta, skipped),[play('coin')]),
        g.set(skipped, 0)]))
    red_cues = []
    for index in range(1, 9):
        red_cues.append(g.control('control_if',g.op('equals',current('BOB.RedCoin.Count'),index),[play(f'game_red_coin_{index}')]))
    # Observe the committed pickup count, independently of broadcast scheduling.
    # A level reset lowers the count and must never replay collection sounds.
    events.append(g.control('control_if',g.op('and',g.op('equals',current('Level.ID'),'BOB'),
        g.op('gt',current('BOB.RedCoin.Count'),previous['BOB.RedCoin.Count'])),red_cues))
    for label, key in [('BOB RED STAR APPEAR','game_red_star_appear'),('BOB CANNON ENTER','game_cannon_enter'),
                       ('BOB CANNON ROTATE','game_cannon_rotate'),('BOB CANNON TILT','game_cannon_tilt'),
                       ('BOB TELEPORT','game_teleport'),('BOB LOOT RELEASE','game_coin_spurt'),
                       ('BOB CANNON AIM','game_cannon_aim')]:
        identity = 'bob_v21_' + label.lower().replace(' ', '_')
        g.hat('event_whenbroadcastreceived',[play(key)],[label,identity])
    g.hat('event_whenbroadcastreceived',[play('game_cannon_fire'),play('game_cannon_flight')],
        ['BOB CANNON FIRE','bob_v21_bob_cannon_fire'])
    celebrating = g.var('Audio.StarCelebrating',0)
    fanfare_running = g.var('Audio.StarFanfareRunning',0)
    dance_actions = ('ACT_STAR_DANCE_EXIT','ACT_STAR_DANCE_NO_EXIT','ACT_STAR_DANCE_WATER')
    def is_dancing(getter):
        return g.op('or',g.op('equals',getter('SM64.Action'),dance_actions[0]),
                    g.op('or',g.op('equals',getter('SM64.Action'),dance_actions[1]),g.op('equals',getter('SM64.Action'),dance_actions[2])))
    # State edges cover collecting an already-owned star, whose count does not rise.
    captured = g.op('and',g.op('equals',current('Star.State'),1),g.op('equals',previous['Star.State'],0))
    events.append(g.control('control_if',captured,[g.set(celebrating,1),play('game_star')]))
    dance_started = g.op('and',is_dancing(current),g.op('not',is_dancing(lambda name:previous[name])))
    events.append(g.control('control_if',dance_started,[g.set(celebrating,1),g.set(fanfare_running,1),
                  _broadcast(g,'game_audio_bc_star_fanfare','GAMEPLAY STAR FANFARE')]))
    voice_crossed = g.op('and',g.op('not',g.op('lt',current('SM64.ActionTimer'),42)),
                        g.op('or',g.op('not',is_dancing(lambda name:previous[name])),g.op('lt',previous['SM64.ActionTimer'],42)))
    events.append(g.control('control_if',g.op('and',is_dancing(current),g.op('and',g.op('equals',current('SM64.ActionState'),0),voice_crossed)),[play('game_here_we_go')]))
    # If a capture is interrupted before landing/dancing, release the music gate.
    star_action=g.op('or',is_dancing(current),g.op('equals',current('SM64.Action'),'ACT_FALL_AFTER_STAR_GRAB'))
    events.append(g.control('control_if',g.op('and',g.op('equals',fanfare_running,0),g.op('and',g.op('equals',current('Star.State'),0),g.op('not',star_action))),[g.set(celebrating,0)]))
    g.hat('event_whenbroadcastreceived',[_sound(g,GAME_AUDIO_DISPLAY_NAMES['game_star_fanfare'],True),
          g.set(fanfare_running,0),g.set(celebrating,0)],['GAMEPLAY STAR FANFARE','game_audio_bc_star_fanfare'])
    hurt=g.op('or',g.op('gt',current('SM64.HurtCounter'),previous['SM64.HurtCounter']),
              g.op('gt',g.op('subtract',previous['SM64.Health'],current('SM64.Health')),255))
    events.append(g.control('control_if',hurt,[play('game_hurt')]))
    events.append(g.control('control_if',g.op('and',g.op('lt',current('SM64.Health'),256),g.op('gt',previous['SM64.Health'],255)),[play('game_death')]))
    events.append(g.control('control_if',g.op('gt',current('SM64.HealthWedges'),previous['SM64.HealthWedges']),[play('game_restore_life')]))
    crossed = g.op('and',g.op('not',g.op('lt',current('Anim.LocalFrame'),55)),g.op('or',g.op('lt',previous['Anim.LocalFrame'],55),g.op('not',g.op('equals',previous['SM64.Action'],'ACT_DEATH_EXIT_LAND'))))
    events.append(g.control('control_if',g.op('and',g.op('equals',current('SM64.Action'),'ACT_DEATH_EXIT_LAND'),crossed),[play('game_mamma_mia')]))
    # The authored punch controller has phases 0/2/4 for first/second/kick.
    for phase, key in [(0,'game_punch'),(2,'game_punch_second'),(4,'game_kick')]:
        attack = g.op('or',g.op('equals',current('SM64.Action'),'ACT_PUNCHING'),g.op('equals',current('SM64.Action'),'ACT_MOVE_PUNCHING'))
        changed = g.op('or',g.op('not',g.op('equals',current('SM64.Action'),previous['SM64.Action'])),g.op('not',g.op('equals',current('SM64.PunchPhase'),previous['SM64.PunchPhase'])))
        phase_match = g.op('and',g.op('equals',current('SM64.PunchPhase'),phase),g.op('not',g.op('equals',current('SM64.ActionArg'),9)))
        events.append(g.control('control_if',g.op('and',attack,g.op('and',phase_match,changed)),[play(key)]))
    for action in ['ACT_JUMP_KICK']:
        events.append(g.control('control_if',g.op('and',g.op('equals',current('SM64.Action'),action),g.op('not',g.op('equals',previous['SM64.Action'],action))),[play('game_kick')]))
    breakdance_action=g.op('or',g.op('equals',current('SM64.Action'),'ACT_PUNCHING'),g.op('equals',current('SM64.Action'),'ACT_MOVE_PUNCHING'))
    breakdance_edge=g.op('or',g.op('not',g.op('equals',previous['SM64.ActionArg'],9)),g.op('not',g.op('equals',previous['SM64.Action'],current('SM64.Action'))))
    events.append(g.control('control_if',g.op('and',breakdance_action,g.op('and',g.op('equals',current('SM64.ActionArg'),9),breakdance_edge)),[play('game_breakdance')]))
    # Native backflip whooshes occur at animation frames 2, 3 and 17.
    for frame in (3,4,18):
        crossed=g.op('and',g.op('not',g.op('lt',current('Anim.LocalFrame'),frame)),g.op('or',g.op('lt',previous['Anim.LocalFrame'],frame),g.op('not',g.op('equals',previous['SM64.Action'],'ACT_BACKFLIP'))))
        flip_active=g.op('and',g.op('equals',current('SM64.Action'),'ACT_BACKFLIP'),g.op('equals',current('Anim.Clip'),'SM64 Backflip'))
        events.append(g.control('control_if',g.op('and',flip_active,crossed),[play('game_flip_spin')]))
    # Native triple-jump spin sounds are on C animation frames 2, 8 and 20.
    # Anim.LocalFrame is one-based in the Scratch controller, hence 3, 9, 21.
    for frame in (3,9,21):
        crossed=g.op('and',g.op('not',g.op('lt',current('Anim.LocalFrame'),frame)),
                     g.op('or',g.op('lt',previous['Anim.LocalFrame'],frame),
                          g.op('not',g.op('equals',previous['SM64.Action'],'ACT_TRIPLE_JUMP'))))
        events.append(g.control('control_if',g.op('and',g.op('equals',current('SM64.Action'),'ACT_TRIPLE_JUMP'),crossed),
                                [play('game_flip_spin')]))
    # Side flip has its own native action cue at C animation frame 6 (Scratch 7).
    side_frame=7
    side_crossed=g.op('and',g.op('not',g.op('lt',current('Anim.LocalFrame'),side_frame)),
                      g.op('or',g.op('lt',previous['Anim.LocalFrame'],side_frame),
                           g.op('not',g.op('equals',previous['SM64.Action'],'ACT_SIDE_FLIP'))))
    events.append(g.control('control_if',g.op('and',g.op('equals',current('SM64.Action'),'ACT_SIDE_FLIP'),side_crossed),
                            [play('game_side_flip_swish')]))
    pipe_rise_played=g.var('Audio.PipeRisePlayed',0,local=True)
    events.append(g.control('control_if',g.op('equals',current('Intro.Active'),0),[g.set(pipe_rise_played,0)]))
    intro_pipe=lambda:g.op('and',g.op('equals',current('Intro.Active'),1),g.op('equals',current('Level.ID'),'GROUNDS'))
    visible_rise = intro_pipe()
    if 'Arrival.Ready' in names:
        visible_rise = g.op('and', visible_rise, g.op('equals', g.var('Arrival.Ready'), 1))
    events.append(g.control('control_if',g.op('and',visible_rise,g.op('and',g.op('equals',current('Intro.Phase'),0),g.op('equals',pipe_rise_played,0))),[play('game_pipe_exit'),g.set(pipe_rise_played,1)]))
    emerge=g.op('equals',current('SM64.Action'),'ACT_EMERGE_FROM_PIPE')
    launch=g.op('and',g.op('not',g.op('lt',current('SM64.ActionTimer'),11)),g.op('or',g.op('lt',previous['SM64.ActionTimer'],11),g.op('not',g.op('equals',previous['SM64.Action'],'ACT_EMERGE_FROM_PIPE'))))
    events.append(g.control('control_if',g.op('and',emerge,launch),[play('game_pipe_jump'),
                  g.control('control_if',g.op('equals',current('Intro.Active'),1),[play('game_pipe_burst')])]))
    pipe_landed=g.op('and',intro_pipe(),g.op('and',g.op('equals',current('Intro.Phase'),3),g.op('not',g.op('equals',previous['Intro.Phase'],3))))
    events.append(g.control('control_if',pipe_landed,[play('game_pipe_laugh'),play('game_pipe_enter')]))
    # Native climb sound crosses frame one (Scratch frame two) once per cycle.
    climb_crossed = g.op('and',g.op('not',g.op('lt',current('Anim.LocalFrame'),2)),g.op('or',g.op('not',g.op('equals',previous['SM64.Action'],'ACT_CLIMBING_POLE')),g.op('or',g.op('lt',previous['Anim.LocalFrame'],2),g.op('lt',current('Anim.LocalFrame'),previous['Anim.LocalFrame']))))
    events.append(g.control('control_if',g.op('and',g.op('equals',current('SM64.Action'),'ACT_CLIMBING_POLE'),climb_crossed),[play('game_tree_climb')]))
    sliding = g.var('Audio.TreeSliding',0)
    events.append(g.set(sliding,g.op('and',g.op('equals',current('SM64.Action'),'ACT_HOLDING_POLE'),g.op('lt',current('Tree.Climb.PolePos'),previous['Tree.Climb.PolePos']))))
    # initiate_painting_warp plays MENU_STAR_SOUND on entry. PAINTING_EJECT
    # belongs to rejected/intangible contact, never to the successful warp.
    painting_entry=g.op('and',g.op('equals',current('Level.ID'),'LOBBY'),g.op('and',g.op('equals',current('Painting.State'),1),g.op('equals',previous['Painting.State'],0)))
    events.append(g.control('control_if',painting_entry,[play('game_painting_warp')]))
    # Follow the preserved actor state, rather than emitting timed ambient barks.
    # Native tethered turn hops use CHOMP1; entering the attack uses CHOMP2.
    tethered=lambda:g.op('equals',current('Chomp.State'),0)
    hop_started=g.op('and',g.op('equals',current('Chomp.Airborne'),1),g.op('equals',previous['Chomp.Airborne'],0))
    turn_hop=g.op('and',tethered(),g.op('and',g.op('equals',current('Chomp.SubAction'),0),
                 g.op('and',hop_started,g.op('and',g.op('equals',current('Chomp.Speed'),.1),g.op('gt',current('Chomp.VelY'),0)))))
    lunge_started=g.op('and',tethered(),g.op('and',g.op('equals',current('Chomp.SubAction'),1),g.op('equals',previous['Chomp.SubAction'],0)))
    # Released counts 0->1 only finish the wait; 2..5 start the four bounces.
    released_hop=g.op('and',g.op('equals',current('Chomp.State'),2),
                     g.op('and',g.op('gt',current('Chomp.ReleaseBounces'),previous['Chomp.ReleaseBounces']),
                     g.op('and',g.op('gt',current('Chomp.ReleaseBounces'),1),g.op('lt',current('Chomp.ReleaseBounces'),6))))
    chomp_events=[g.control('control_if',g.op('or',turn_hop,released_hop),[play('game_chomp_bark')]),
                  g.control('control_if',lunge_started,[play('game_chomp_lunge')]),
                  g.control('control_if',g.op('gt',current('Chomp.PostHits'),previous['Chomp.PostHits']),[play('game_chomp_post')]),
                  g.control('control_if',g.op('and',g.op('equals',current('Chomp.GateBroken'),1),g.op('equals',previous['Chomp.GateBroken'],0)),[play('game_chomp_gate')])]
    events.append(g.control('control_if',g.op('and',g.op('equals',current('Level.ID'),'BOB'),g.op('equals',current('Chomp.Active'),1)),chomp_events))
    for name in ('Door.State','RoomDoor.State'):
        events.append(g.control('control_if',g.op('and',g.op('gt',current(name),0),g.op('equals',previous[name],0)),[play('game_door_open')]))
        events.append(g.control('control_if',g.op('and',g.op('equals',current(name),0),g.op('gt',previous[name],0)),[play('game_door_close')]))
    # Native footstep frames; Scratch's animation index is one-based.
    for clip, frames, sound in [('Walking (Not running)',(11,50),'step'),('Running 1',(10,46),'step'),('Tiptoe',(15,73),'tiptoe')]:
        for frame in frames:
            crossed=g.op('and',g.op('not',g.op('lt',current('Anim.LocalFrame'),frame)),
                         g.op('or',g.op('lt',previous['Anim.LocalFrame'],frame),g.op('lt',current('Anim.LocalFrame'),previous['Anim.LocalFrame'])))
            valid=g.op('and',g.op('equals',current('Anim.Clip'),clip),g.op('equals',previous['Anim.Clip'],clip))
            events.append(g.control('control_if',g.op('and',valid,crossed),terrain_sound(sound)))
    sync = lambda:[g.set(previous[name],current(name)) for name in fields]
    ready=g.var('Audio.GameplayReady',0,local=True)
    g.hat('event_whenbroadcastreceived',[g.set(ready,1)],['TITLE INTRO GAMEPLAY FADE IN','v23_bc_gameplay_fade_in'])
    g.hat('event_whenbroadcastreceived',[g.set(ready,0),g.set(pipe_rise_played,0),g.set(backflip_land_armed,0)],['TITLE INTRO RUN','titleintro_bc_run'])
    valid=g.op('and',g.op('equals',ready,1),g.op('and',g.op('equals',current('TitleIntro.Active'),0),g.op('equals',current('Level.ID'),previous['Level.ID'])))
    g.hat('event_whenflagclicked',[g.set(ready,0),g.set(pipe_rise_played,0),g.set(backflip_land_armed,0),g.set(sliding,0),g.set(celebrating,0),g.set(fanfare_running,0)]+sync()+[g.control('control_forever',None,
          [g.set(sliding,0),g.control('control_if_else',valid,events,[g.set(backflip_land_armed,0)])]+sync()+[g.node('control_wait',{'DURATION':1/30})])])

    music, music_player = _add_level_music(project, sound_records)
    tree, stage, tg = _sprite(project,TREE_AUDIO_NAME)
    tree['sounds'] = [deepcopy(sound_records[GAME_AUDIO_DISPLAY_NAMES['game_tree_slide']])]
    tree['volume'] = 0
    tg.hat('event_whenflagclicked',[tg.node('sound_setvolumeto',{'VOLUME':0}),tg.control('control_forever',None,[
        tg.control('control_if_else',tg.op('equals',tg.var('Audio.TreeSliding'),1),
                   [tg.node('sound_setvolumeto',{'VOLUME':100})],
                   [tg.node('sound_setvolumeto',{'VOLUME':0})]),tg.node('control_wait',{'DURATION':1/30})])])
    tg.hat('event_whenflagclicked',[tg.control('control_forever',None,[
        tg.control('control_if_else',tg.op('equals',tg.var('Audio.TreeSliding'),1),
                   [_sound(tg,GAME_AUDIO_DISPLAY_NAMES['game_tree_slide'],True)],
                   [tg.node('control_wait',{'DURATION':1/30})])])])
    # Media belongs to its playback sprites. Each blob is stored once in ZIP.
    game_names = set(GAME_AUDIO_DISPLAY_NAMES.values())
    stage['sounds'] = [s for s in stage['sounds'] if s['name'] not in game_names]
    for sprite in (target, music, tree):
        for block in sprite['blocks'].values():
            if block['opcode'] == 'control_forever':
                block['inputs'].pop('CONDITION', None)
    actors=[music_player,_add_recovery_audio(project,sound_records),_add_intro_music(project,sound_records),_add_goomba_audio(project,sound_records)]+_add_grounds_birds(project,rom,sound_records)
    for sprite in actors:
        for block in sprite['blocks'].values():
            if block['opcode']=='control_forever':block['inputs'].pop('CONDITION',None)
    return dict(gameplay_sound_effects=len(target['sounds']),music_sequences=[3,4,30],
                monitored_state_fields=fields,observer_interval_seconds=1/30,
                music_policy='Native BOB/lobby intro and loop; sequence 30 phrase during arrival, isolated from level music stops; grounds gameplay has no background music',
                star_collection_policy='Native pickup cue; sequence 1 on dance entry; Here We Go at action timer 42; background music muted until fanfare completes',
                chain_chomp_policy='Native turn-hop/released-hop bark and attack bark; post-hit and gate-break state edges; active BOB actor only',
                garden_ambience_policy='Three native repeating bird programs at ROM emitter positions; US distance volume curve; muted outside grounds and during title',
                pipe_policy='Rise on grounds intro phase 0; Yahoo and hit cue at emergence timer 11; laugh and lower cue at phase 3',
                backflip_landing_policy='Native HAHA once during grounded backflip landing with no analog input; track origin across authored triple-jump landing aliases; clear on cancellation/title/level change',
                goomba_policy='Per-actor alert, chase footstep frames 2/17, stomp and death state edges; normal BOB size and distance gating',
                jump_audio_policy='US-native jump cues: random Yah/Wah/Hoo for single/side/wall-kick, fixed Hoohoo double, 5-entry Yahoo/Waha/Yippee triple pool, fixed Yah backflip, fixed Yahoo long jump; triple/backflip spin frames and side-flip frame-6 cue restored',
                ground_pound_policy='Native spin on action entry, WAH on descent-phase edge, terrain-heavy landing once; ordinary landing suppressed for this impact',
                low_health_policy='ACT_PANTING below 0x500 health; native animation frame 1 (Scratch frame 2); one randomly selected voice per cycle',
                ledge_audio_policy='ACT_LEDGE_GRAB WHOA; slow climb EEUH at action timer 10; fast climb UH2 on entry and terrain landing at native anim frame 8 (Scratch local frame 9); suppress the duplicate grounded-edge cue at fast-climb completion',
                painting_entry_policy='MENU_STAR_SOUND once on successful Painting.State 0-to-1 entry; eject sound excluded',
                startup_policy='Effects/music disabled until TITLE INTRO GAMEPLAY FADE IN after engine initialization; reset on green flag and TITLE INTRO RUN',
                limitations=['Audio synthesis approximates console resampling and reverb.',
                             'Other ambient actors, water actions and several action-specific effects remain pending.',
                             'Chain Chomp uses actor activation gating; native distance pan/attenuation and release puzzle jingle remain pending.',
                             'Bird ambience preserves program cycles and distance volume; dynamic pan and the native behind-Mario camera mute are pending.',
                             'Goomba cues use per-actor state/animation edges and distance gating; native bank priority and distance pan/attenuation remain pending.',
                             'Door close cue currently follows the preserved door state returning to zero.',
                             'Tree sliding uses a bounded native sample loop; moving-speed pitch modulation remains pending.'])


def apply_game_features(path: Path, rom: bytes, credit=PORT_CREDIT):
    with zipfile.ZipFile(path) as source:
        project=json.loads(source.read('project.json'))
        old_media={c['md5ext'] for t in project['targets'] if t['name']==NOTICE_NAME for c in t['costumes']}
        media=add_project_credit(project,credit)
        report=add_game_audio_observers(project,rom)
        from sm64_scratch.patches.patch_game_presentation import apply_game_presentation
        presentation = apply_game_presentation(project)
        from sm64_scratch.patches.patch_gameplay_v13 import apply_gameplay_fixes
        gameplay_fixes = apply_gameplay_fixes(project)
        from sm64_scratch.patches.patch_damage_camera_hud import apply_damage_camera_hud
        damage_camera_hud = apply_damage_camera_hud(project)
        from sm64_scratch.patches.patch_beta010 import apply_beta010
        beta010 = apply_beta010(project)
        if project.get('extensionData', {}).get('arrival_flight_fix'):
            from sm64_scratch.patches.patch_arrival_flight import patch_arrival_flight_audio
            patch_arrival_flight_audio(project)
        project.setdefault('extensionData',{})['porter_game_features']={'version':16,'credit':credit,'beta':BETA_VERSION}
        referenced={a['md5ext'] for t in project['targets'] for a in t.get('costumes',[])+t.get('sounds',[])}
        tmp=path.with_name(path.name+'.features.tmp')
        with zipfile.ZipFile(tmp,'w')as out:
            for item in source.infolist():
                if item.filename=='project.json':_write_project(out,item,json.dumps(project,separators=(',',':')).encode())
                elif item.filename not in old_media or item.filename in referenced:
                    _write_project(out,item,source.read(item.filename))
            for name,data in media.items():
                if name not in out.namelist():out.writestr(name,data)
    tmp.replace(path)
    report['game_presentation']=presentation
    report['gameplay_v13']=gameplay_fixes
    report['castle_door_v15']=project['extensionData']['castle_door_v15']
    report['damage_camera_hud_v14']=damage_camera_hud
    report['beta010_v16']=beta010
    report['credit']=credit
    return report
