"""Decode gameplay audio from the local US ROM, without reference recordings."""
from sm64_scratch.paths import METADATA_DIR
from sm64_scratch.decoders.decode_audio import _Sequence, _sequence_data, _sfx_address, _render_events, _bank_data, _instrument_sound
import struct

GAME_AUDIO_DISPLAY_NAMES = {
    'game_teleport': 'Local Teleport (ROM)',
    'game_red_star_appear': 'Red Coin Star Appears (ROM)',
    'game_cannon_enter': 'Cannon Rise (ROM)',
    'game_cannon_rotate': 'Cannon Rotate (ROM)',
    'game_cannon_tilt': 'Cannon Tilt (ROM)',
    'game_cannon_aim': 'Cannon Aim (ROM)',
    'game_cannon_fire': 'Cannon Fire (ROM)',
    'game_cannon_flight': 'Cannon Flying Fast (ROM)',
    'game_intro_cutscene_music': 'Intro Cutscene Music (ROM)',
    'game_camera_turn': 'Camera Turn (ROM)',
    'game_camera_zoom_in': 'Camera Zoom In (ROM)',
    'game_camera_zoom_out': 'Camera Zoom Out (ROM)',
    'game_camera_buzz': 'Camera Blocked (ROM)',
    'game_hurt': 'Mario Hurt (ROM)', 'game_death': 'Mario Death (ROM)',
    'game_punch': 'Mario Punch (ROM)', 'game_jump_voice': 'Mario Jump Voice (ROM)',
    'game_jump_voice_2': 'Mario Jump Wah (ROM)', 'game_jump_voice_3': 'Mario Jump Hoo (ROM)',
    'game_double_jump_voice': 'Mario Double Jump Voice (ROM)',
    'game_triple_jump_voice': 'Mario Triple Jump Voice (ROM)',
    'game_triple_jump_random_1': 'Mario Triple Jump Yahoo 1 (ROM)',
    'game_triple_jump_random_2': 'Mario Triple Jump Yahoo 2 (ROM)',
    'game_triple_jump_random_3': 'Mario Triple Jump Yahoo 3 (ROM)',
    'game_triple_jump_random_4': 'Mario Triple Jump Waha (ROM)',
    'game_triple_jump_random_5': 'Mario Triple Jump Yippee (ROM)',
    'game_side_flip_swish': 'Mario Side Flip Swish (ROM)',
    'game_door_open': 'Wood Door Open (ROM)', 'game_door_close': 'Wood Door Close (ROM)',
    'game_star': 'Star Collection (ROM)',
    'game_star_fanfare': 'Star Collection Fanfare (ROM)',
    'game_here_we_go': 'Mario Here We Go (ROM)',
    'game_painting_ripple': 'Painting Ripple (ROM)',
    'game_painting_warp': 'Painting Warp (ROM)',
    'game_punch_second': 'Mario Second Punch (ROM)', 'game_kick': 'Mario Kick (ROM)',
    'game_tree_climb': 'Tree Climb (ROM)', 'game_tree_slide': 'Tree Slide Loop (ROM)',
    'game_chomp_bark': 'Chain Chomp Bark (ROM)',
    'game_chomp_lunge': 'Chain Chomp Lunge (ROM)',
    'game_chomp_post': 'Wooden Post Pound (ROM)',
    'game_chomp_gate': 'Chain Chomp Gate Break (ROM)',
    'game_flip_spin': 'Mario Flip Spin (ROM)',
    'game_ground_pound_voice': 'Mario Ground Pound Wah (ROM)',
    'game_bowser_death': 'Bowser Death Transition Laugh (ROM)',
    'game_mamma_mia': 'Mario Mamma Mia (ROM)',
    'game_restore_life': 'Power Meter Restore (ROM)',
    'game_coin_spurt': 'Coin Spurt (ROM)',
    'game_coin_bounce': 'Coin Bounce (ROM)',
    'game_panting_1': 'Mario Panting 1 (ROM)',
    'game_panting_2': 'Mario Panting 2 (ROM)',
    'game_panting_3': 'Mario Panting 3 (ROM)',
    'game_ledge_whoa': 'Mario Ledge Grab Whoa (ROM)',
    'game_ledge_eeuh': 'Mario Slow Ledge Climb Eeuh (ROM)',
    'game_ledge_uh2': 'Mario Fast Ledge Climb Uh (ROM)',
    'game_backflip_land': 'Mario Backflip Landing Laugh (ROM)',
    'game_breakdance': 'Mario Breakdance (ROM)',
    'game_pipe_exit': 'Pipe Rise (ROM)', 'game_pipe_enter': 'Pipe Lower (ROM)',
    'game_pipe_jump': 'Mario Pipe Jump (ROM)',
    'game_pipe_burst': 'Mario Pipe Burst (ROM)',
    'game_pipe_laugh': 'Mario Pipe Landing Laugh (ROM)',
    'game_goomba_step': 'Goomba Footstep (ROM)',
    'game_goomba_alert': 'Goomba Alert (ROM)',
    'game_goomba_stomp': 'Goomba Stomp (ROM)',
    'game_goomba_death': 'Goomba Death (ROM)',
    'game_birds_1': 'Grounds Birds 1 Loop (ROM)',
    'game_birds_2': 'Grounds Birds 2 Loop (ROM)',
    'game_birds_3': 'Grounds Birds 3 Loop (ROM)',
}
for i in range(1, 9):
    GAME_AUDIO_DISPLAY_NAMES[f'game_red_coin_{i}'] = f'Red Coin {i} (ROM)'
for action in ('jump', 'land', 'step', 'tiptoe', 'heavy_land'):
    for terrain in range(8):
        label = 'Heavy Landing' if action == 'heavy_land' else action.title()
        GAME_AUDIO_DISPLAY_NAMES[f'game_{action}_{terrain}'] = f'{label} Terrain {terrain} (ROM)'
for level in ('bob', 'lobby'):
    for part in ('intro', 'loop'):
        GAME_AUDIO_DISPLAY_NAMES[f'game_music_{level}_{part}'] = f'{level.title()} Music {part.title()} (ROM)'


def sequence_banks(rom, sequence_id):
    """The US bank-set table stores banks in reverse default-selection order."""
    base = 0x7CC620
    offset = struct.unpack_from('>H', rom, base + sequence_id * 2)[0]
    count = rom[base + offset]
    banks = list(rom[base + offset + 1:base + offset + count + 1])
    if not banks or any(bank > 0x25 for bank in banks):
        raise ValueError('Unexpected US sequence bank set')
    return banks[::-1]


def join_continuous_notes(rom, player):
    """Retain native sample playback across same-sample continuous-layer notes."""
    original = player.events
    joined, mapping, banks = [], {}, {}
    stable = ('bank','instrument','velocity','volume','pan','freq','envelope',
              'release','reverb','vibrate','vibextent','channel')
    def sample(event):
        bank = event['bank']
        if bank not in banks: banks[bank] = _bank_data(rom, bank)
        instruments = banks[bank][2]
        if event['instrument'] >= len(instruments): return None
        instrument = instruments[event['instrument']]
        key = max(event['key'],event['portamento'][1]) if event['portamento'] else event['key']
        return _instrument_sound(instrument,key)
    merged = 0
    for index, event in enumerate(original):
        prior_index = event.get('continuous_from')
        prior = mapping.get(prior_index)
        if prior is not None and prior['end'] == event['tick'] and all(prior[k] == event[k] for k in stable) and sample(prior) == sample(event):
            if 'pitch_segments' not in prior:
                prior['pitch_segments'] = [{k:prior[k] for k in ('tick','end','key','portamento')}]
            prior['pitch_segments'].append({k:event[k] for k in ('tick','end','key','portamento')})
            prior['end'] = event['end']
            mapping[index] = prior
            merged += 1
        else:
            joined.append(event); mapping[index] = event
    player.events = joined
    return merged


def build_intro_cutscene_music(rom):
    """Keep the final musical phrase through the shortened arrival and release."""
    from pathlib import Path
    import json
    flight = json.loads((METADATA_DIR / 'arrival_flight.json').read_text())
    # The camera retains only 90 flight ticks plus 270 pipe ticks. Use the
    # matching-length final score passage, rather than a 6.9-second suffix
    # that falls silent halfway through the scene. Tempo and pitch are native.
    duration = (flight['end_tick'] - flight['start_tick'] + 270) / 30 + .6
    banks = sequence_banks(rom, 30)
    player = _Sequence(_sequence_data(rom, 30), banks[0]).run(max_ticks=6000)
    joined = join_continuous_notes(rom, player)
    end = max(player.times[e['end']] for e in player.events) + .6
    start = max(0, end - duration)
    asset = _render_events(rom, player, start, end)
    asset[1].update(sequence_id=30, sound_banks=banks, score_start_seconds=start,
                    score_end_seconds=end, continuous_notes_joined=joined,
                    timing='Final phrase aligned to the shortened 12-second scene plus natural release',
                    scene_seconds=duration - .6, release_seconds=.6)
    return asset


def build_game_audio_assets(rom):
    effects = {'game_camera_turn': 0x700F0080, 'game_camera_zoom_in': 0x70060080,
               'game_camera_zoom_out': 0x70070080, 'game_camera_buzz': 0x700E0080,
               'game_hurt': 0x240AFF80, 'game_death': 0x2415FF80,
               'game_punch': 0x241E8080, 'game_jump_voice': 0x24008080,
               'game_jump_voice_2': 0x24018080, 'game_jump_voice_3': 0x24028080,
               'game_double_jump_voice': 0x24038080, 'game_triple_jump_voice': 0x24048080,
               'game_triple_jump_random_1': 0x242B8080, 'game_triple_jump_random_2': 0x242C8080,
               'game_triple_jump_random_3': 0x242D8080, 'game_triple_jump_random_4': 0x242E8080,
               'game_triple_jump_random_5': 0x242F8080, 'game_side_flip_swish': 0x045A8080,
               'game_door_open': 0x3004C080, 'game_door_close': 0x3005C080,
               'game_star': 0x701EFF80, 'game_here_we_go': 0x240C8080, 'game_punch_second': 0x24248080,
               'game_kick': 0x241F8080, 'game_tree_climb': 0x043A8080,
               'game_tree_slide': 0x14128000, 'game_painting_ripple': 0x39280080,
               'game_painting_warp': 0x701EFF80,
               'game_chomp_bark': 0x30390080, 'game_chomp_lunge': 0x303A0080,
               'game_chomp_post': 0x3065C080, 'game_chomp_gate': 0x300F0080,
               'game_flip_spin': 0x04378080, 'game_backflip_land': 0x24118080, 'game_breakdance': 0x241F8080,
               'game_ground_pound_voice': 0x24228080,
               'game_bowser_death': 0x70188080, 'game_mamma_mia': 0x24208080,
               'game_restore_life': 0x700D0080,
               'game_coin_spurt': 0x38300080, 'game_coin_bounce': 0x30364080,
               'game_panting_1': 0x24188080, 'game_panting_2': 0x24198080,
               'game_panting_3': 0x241A8080,
               # mario_actions_automatic.c: ACT_LEDGE_GRAB / slow / fast climb.
               'game_ledge_whoa': 0x2408C080, 'game_ledge_eeuh': 0x24098080,
               'game_ledge_uh2': 0x2413D080,
               'game_pipe_exit': 0x7017A080, 'game_pipe_enter': 0x7016A080,
               'game_pipe_jump': 0x24048080, 'game_pipe_burst': 0x0444A080,
               'game_pipe_laugh': 0x24118080,
               'game_goomba_step': 0x50200080, 'game_goomba_alert': 0x502F0080,
               'game_goomba_stomp': 0x50308080, 'game_goomba_death': 0x5060B080,
               'game_birds_1': 0x90524000, 'game_birds_2': 0x80504000,
               'game_birds_3': 0x50514000}
    for action, first in [('jump', 0), ('land', 8), ('step', 16), ('tiptoe', 32), ('heavy_land', 96)]:
        for terrain in range(8):
            effects[f'game_{action}_{terrain}'] = 0x04008080 + ((first + terrain) << 16)
    effects.update({'game_teleport': 0x0457C080,
                    'game_red_star_appear': 0x3057FF90, 'game_cannon_enter': 0x500DF080,
                    'game_cannon_rotate': 0x500EF080, 'game_cannon_tilt': 0x500FF080,
                    'game_cannon_aim': 0x1D192000, 'game_cannon_fire': 0x501A5080,
                    'game_cannon_flight': 0x04568080})
    effects.update({f'game_red_coin_{i + 1}': 0x78289080 + (i << 16) for i in range(8)})
    assets = {}
    data = _sequence_data(rom, 0)
    for key, sound_id in effects.items():
        address = _sfx_address(data, sound_id)
        player = _Sequence(data, 0, sfx_address=address,
                           sfx_sound_id=sound_id if key.startswith('game_red_coin_') else None).run(
            max_ticks=4000 if key.startswith('game_birds_') else 96 if key == 'game_tree_slide' else 24 if key == 'game_cannon_aim' else 1200, detect_loop=False)
        joined = join_continuous_notes(rom, player)
        if key.startswith('game_birds_'):
            # Read a complete repeated program cycle, including prior release
            # tails at the seam. Do not cut an arbitrary number of seconds.
            loop = _bird_program_cycle(player)
            assets[key] = _render_events(rom,player,player.times[loop],player.times[loop*2])
            assets[key][1].update(native_loop_ticks=loop,loop_interval_ticks=[loop,loop*2])
        else:
            assets[key] = _render_events(rom, player, end_seconds=player.times[-1] if key in ('game_tree_slide', 'game_cannon_aim') else None)
        assets[key][1]['continuous_sample_restarts_prevented'] = joined
        if not assets[key][1]['events'] or not assets[key][1]['peak']:
            raise ValueError('Empty ROM sound effect: ' + key)
        assets[key][1].update(sound_id=f'{sound_id:#010x}', sequence_address=address)
    # Native star celebration is sequence 1, not the unrelated short-star effect.
    banks = sequence_banks(rom, 1)
    player = _Sequence(_sequence_data(rom, 1), banks[0]).run(max_ticks=480)
    if not player.events or player.loop:
        raise ValueError('Native star fanfare is missing or unexpectedly loops')
    end = player.times[max(e['end'] for e in player.events)] + .9
    wav, report = _render_events(rom, player, end_seconds=end)
    # The M64 player can stay idle after its final note. Retain its real tail,
    # rather than exporting the interpreter's otherwise long silent run.
    from io import BytesIO
    import wave
    import numpy as np
    with wave.open(BytesIO(wav)) as source:
        pcm = np.frombuffer(source.readframes(source.getnframes()),dtype='<i2').reshape(-1,2)
    nonzero = np.flatnonzero(np.any(pcm != 0,axis=1))
    if not len(nonzero): raise ValueError('Native star fanfare rendered silent')
    pcm = pcm[:min(len(pcm),int(nonzero[-1])+1+640)]
    out = BytesIO()
    with wave.open(out,'wb')as target:
        target.setnchannels(2);target.setsampwidth(2);target.setframerate(32000);target.writeframes(pcm.tobytes())
    report.update(sequence_id=1,sound_banks=banks,duration_seconds=len(pcm)/32000,
                  terminal_note_tick=max(e['end'] for e in player.events),
                  idle_sequence_silence_trimmed=True)
    assets['game_star_fanfare'] = (out.getvalue(),report)
    assets['game_intro_cutscene_music'] = build_intro_cutscene_music(rom)
    for level, sequence_id in [('bob', 3), ('lobby', 4)]:
        banks = sequence_banks(rom, sequence_id)
        player = _Sequence(_sequence_data(rom, sequence_id), banks[0]).run()
        if not player.loop:
            raise ValueError('Gameplay music loop not found: ' + level)
        start, end = (player.times[tick] for tick in player.loop)
        for part, a, b in [('intro', 0, start), ('loop', start, end)]:
            key = f'game_music_{level}_{part}'
            assets[key] = _render_events(rom, player, a, b)
            assets[key][1].update(sequence_id=sequence_id, sound_banks=banks,
                                  loop_ticks=list(player.loop))
    return assets


def _bird_program_cycle(player):
    """Find two matching complete event cycles in the ROM ambient program."""
    events=player.events
    signature=lambda e:tuple(e[k] for k in ('key','instrument','bank','velocity','pan','volume'))+(e['end']-e['tick'],)
    for count in range(1,len(events)//2+1):
        period=events[count]['tick']-events[0]['tick']
        if all(signature(events[i])==signature(events[i+count]) and
               events[i+count]['tick']-events[i]['tick']==period for i in range(count)):
            if period*2<len(player.times):return period
    raise ValueError('No complete repeated ROM bird program cycle found')


def grounds_bird_emitters(rom):
    """Read the three ambient emitter positions from US level-script commands."""
    import json
    from pathlib import Path
    translation=json.loads((METADATA_DIR / 'grounds.json').read_text())['world_translation']
    rows=[]
    for index,offset in enumerate((0x454738,0x454750,0x454768)):
        if rom[offset-4:offset]!=bytes.fromhex('24181f00') or rom[offset+13]!=index:
            raise ValueError('Unexpected ROM ambient bird object command')
        rows.append([v/100+d for v,d in zip(struct.unpack_from('>hhh',rom,offset),translation)])
    return rows


def terrain_sound_table(rom):
    rows = [list(rom[0xE8AA0 + row * 6:0xE8AA0 + (row + 1) * 6]) for row in range(7)]
    if any(value > 7 for row in rows for value in row):
        raise ValueError('Unexpected native terrain sound table')
    return rows
