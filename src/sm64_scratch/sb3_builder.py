"""Write locally rebuilt title media and runtime data into a clean Scratch base."""
from __future__ import annotations
import copy, hashlib, json, zipfile, wave
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from PIL import Image
BASE_NOTICE_BLOCK_IDS = {'notice_greenflag', 'notice_switchbackdrop'}

def _write_project(target, item, payload):
    """Write project.json without recompressing the very large JSON blob.

    Scratch and TurboWarp accept a stored ZIP member.  Keeping the generated
    project uncompressed makes local ROM builds finish promptly; image/media
    members keep their original compression and metadata.
    """
    if item.filename == 'project.json':
        item = copy.copy(item)
        item.compress_type = zipfile.ZIP_STORED
    target.writestr(item, payload)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _audio_sound_entry(name: str, data: bytes) -> dict:
    """Create the compact Scratch sound record for a generated WAV."""
    # Scratch validates sound.assetId/md5ext as a 32-character MD5 digest.
    # The builders use SHA-256 for texture/model reports, but media records
    # must follow Scratch's own asset naming convention.
    digest = hashlib.md5(data).hexdigest()
    with wave.open(BytesIO(data), 'rb') as wav:
        rate = wav.getframerate()
        sample_count = wav.getnframes()
    return {
        'name': name,
        'assetId': digest,
        'dataFormat': 'wav',
        'format': '',
        'rate': rate,
        'sampleCount': sample_count,
        'md5ext': digest + '.wav',
    }


def add_audio_assets(sb3_path: Path, audio_assets: dict[str, tuple[bytes, dict]]):
    """Embed local ROM audio; music and greeting run on independent scripts."""
    if not audio_assets:
        return {}
    from sm64_scratch.decoders.decode_audio import TITLE_AUDIO_DISPLAY_NAMES
    from sm64_scratch.decoders.decode_game_audio import GAME_AUDIO_DISPLAY_NAMES
    display_names = {**TITLE_AUDIO_DISPLAY_NAMES, **GAME_AUDIO_DISPLAY_NAMES}
    generated_names = set(display_names.values()) | {
        'SM64 Title Music (ROM)', 'SM64 Intro Music (ROM)', 'SM64 Logo Voice (ROM)'}
    with zipfile.ZipFile(sb3_path, 'r') as source:
        project = json.loads(source.read('project.json'))
        stage = next(t for t in project['targets'] if t.get('isStage'))
        stage.setdefault('sounds', [])
        stale_media = {s.get('md5ext') for s in stage['sounds']
                       if s.get('name') in generated_names and s.get('md5ext')}
        stage['sounds'] = [s for s in stage['sounds'] if s.get('name') not in generated_names]
        records = {}
        for key, (data, report) in audio_assets.items():
            entry = _audio_sound_entry(display_names[key], data)
            stage['sounds'].append(entry)
            records[key] = (entry, data, report)
        blocks = stage.setdefault('blocks', {})
        # Remove generated trees, reconnecting any existing backdrop chain.
        generated = {k for k in blocks if k.startswith('v13_audio_')}
        for key, block in blocks.items():
            if key in generated: continue
            nxt = block.get('next')
            while nxt in generated: nxt = blocks[nxt].get('next')
            block['next'] = nxt
            if nxt in blocks: blocks[nxt]['parent'] = key
        for key in generated: del blocks[key]
        counter = 0

        def make(opcode, inputs=None, fields=None):
            nonlocal counter
            counter += 1
            key = f'v13_audio_block_{counter:04d}'
            blocks[key] = {'opcode': opcode, 'next': None, 'parent': None,
                           'inputs': inputs or {}, 'fields': fields or {},
                           'shadow': False, 'topLevel': False}
            for value in blocks[key]['inputs'].values():
                if len(value) > 1 and isinstance(value[1], str) and value[1] in blocks:
                    blocks[value[1]]['parent'] = key
            return key

        def link(keys):
            for prev, nxt in zip(keys, keys[1:]):
                blocks[prev]['next'] = nxt
                blocks[nxt]['parent'] = prev
            return keys[0] if keys else None

        def append_after(hat, keys):
            if not hat or not keys: return
            tail = hat
            while blocks[tail].get('next'): tail = blocks[tail]['next']
            head = link(keys)
            blocks[tail]['next'] = head
            blocks[head]['parent'] = tail

        def hat(broadcast, name, x, y):
            key = make('event_whenbroadcastreceived', fields={
                'BROADCAST_OPTION': [name, broadcast]})
            blocks[key].update(topLevel=True, x=x, y=y)
            return key

        def play(key, wait=False):
            # Real sound-menu shadows follow Scratch's schema and VM format.
            shadow = make('sound_sounds_menu', fields={'SOUND_MENU': [records[key][0]['name'], None]})
            blocks[shadow]['shadow'] = True
            return make('sound_playuntildone' if wait else 'sound_play',
                        {'SOUND_MENU': [1, shadow]})

        def wait(seconds):
            return make('control_wait', {'DURATION': [1, [4, str(seconds)]]})

        variables = stage.setdefault('variables', {})
        names = {v[0]: k for k, v in variables.items()}
        for key, name in [('v13_audio_prev_hand', 'Audio.PreviousHand'),
                          ('v13_audio_prev_grab', 'Audio.PreviousGrab'),
                          ('v13_audio_title_ending', 'Audio.TitleEnding')]:
            variables[key] = [name, 0]; names[name] = key

        def var(name):
            if name not in names: raise ValueError('Missing intro variable: '+name)
            return [3, [12, name, names[name]], [10, '0']]

        def eq(name, number=0):
            return make('operator_equals', {'OPERAND1': var(name), 'OPERAND2': [1, [4,str(number)]]})

        def gt(name):
            return make('operator_gt', {'OPERAND1': var(name), 'OPERAND2': [1, [4,'0']]})

        def setvar(name, value):
            return make('data_setvariableto', {'VALUE': value}, {'VARIABLE':[name,names[name]]})

        existing_hats = {}
        for key, block in list(blocks.items()):
            if block.get('opcode') == 'event_whenbroadcastreceived':
                bc = block.get('fields',{}).get('BROADCAST_OPTION',[None,None])[1]
                if bc in ('titleintro_bc_logo','titleintro_bc_head','titleintro_bc_end'):
                    existing_hats[bc] = key
        append_after(hat('titleintro_bc_logo','TITLE INTRO LOGO',760,100), [play('logo_sound')])
        greeting = make('control_if', {'CONDITION':[2,make('operator_and', {
            'OPERAND1':[2,eq('TitleIntro.Active',1)], 'OPERAND2':[2,eq('Audio.TitleEnding',0)]})],
                                       'SUBSTACK':[2,play('hello_voice')]})
        append_after(hat('titleintro_bc_head','TITLE INTRO HEAD',760,240), [wait(20/30),greeting])
        # The exit cue belongs to the start of the fade, before gameplay
        # initialization. Independent receive scripts do not wait behind the
        # Stage's heavy world-building chain.
        fade = hat('v23_bc_title_exit_fade', 'TITLE INTRO EXIT FADE', 760, 360)
        append_after(fade, [setvar('Audio.TitleEnding', [1, [4, '1']]),
                            make('sound_stopallsounds'), play('start_star')])

        music = hat('titleintro_bc_head','TITLE INTRO HEAD',760,180)
        repeat = make('control_repeat_until', {
            'CONDITION':[2,make('operator_or', {'OPERAND1':[2,eq('TitleIntro.Active')],
                                                'OPERAND2':[2,eq('Audio.TitleEnding',1)]})],
            'SUBSTACK':[2,play('title_music_loop',wait=True)]})
        append_after(music,[play('title_music_intro',wait=True),repeat])

        # Play interaction sounds on state edges, once per action.
        monitor = hat('titleintro_bc_head','TITLE INTRO HEAD',1040,180)
        body = []
        for current, previous, event, enter in (
                ('TitleIntro.HandVisible','Audio.PreviousHand','hand_appear',True),
                ('TitleIntro.HandVisible','Audio.PreviousHand','hand_disappear',False),
                ('TitleIntro.SelectedGrab','Audio.PreviousGrab','face_pinch',True),
                ('TitleIntro.SelectedGrab','Audio.PreviousGrab','face_release',False)):
            condition = make('operator_and', {
                'OPERAND1':[2,gt(current) if enter else eq(current)],
                'OPERAND2':[2,eq(previous) if enter else gt(previous)]})
            body.append(make('control_if',{'CONDITION':[2,condition],'SUBSTACK':[2,play(event)]}))
        body.extend([setvar('Audio.PreviousHand',var('TitleIntro.HandVisible')),
                     setvar('Audio.PreviousGrab',var('TitleIntro.SelectedGrab')),wait(1/60)])
        loop = make('control_repeat_until', {'CONDITION':[2,eq('TitleIntro.Active')],
                                           'SUBSTACK':[2,link(body)]})
        append_after(monitor,[setvar('Audio.PreviousHand',[1,[4,'0']]),
                              setvar('Audio.PreviousGrab',[1,[4,'0']]),loop])
        # Stop a pending music/greeting thread on a green-flag restart.
        reset = make('event_whenflagclicked')
        blocks[reset].update(topLevel=True,x=760,y=40)
        append_after(reset,[setvar('Audio.TitleEnding',[1,[4,'0']]),make('sound_stopallsounds')])
        for direction in ('in', 'out'):
            key = 'camera_zoom_' + direction
            if key in records:
                name = 'TITLE INTRO CAMERA ZOOM ' + direction.upper()
                bc = 'titleintro_bc_camera_zoom_' + direction
                stage.setdefault('broadcasts', {})[bc] = name
                append_after(hat(bc, name, 1320, 180 if direction == 'in' else 320), [play(key)])

        referenced_media = {s.get('md5ext') for t in project['targets']
                            for s in t.get('sounds',[])}
        stale_media -= referenced_media
        payload = json.dumps(project,ensure_ascii=False,separators=(',',':')).encode('utf-8')
        temporary = sb3_path.with_name(sb3_path.name+'.audio.tmp')
        try:
            with zipfile.ZipFile(temporary,'w') as target:
                for item in source.infolist():
                    if item.filename == 'project.json': _write_project(target,item,payload)
                    elif item.filename not in stale_media: _write_project(target,item,source.read(item.filename))
                for entry,data,_report in records.values():
                    if entry['md5ext'] not in target.namelist(): target.writestr(entry['md5ext'],data)
            temporary.replace(sb3_path)
        finally:
            if temporary.exists(): temporary.unlink()
    return {key: report for key,(_entry,_data,report) in records.items()}


def inspect_clean_base(path: Path):
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError('Corrupt BASE.sb3: ' + bad)
        project = json.loads(archive.read('project.json'))
    if any(items[1] for target in project.get('targets', [])
           for items in target.get('lists', {}).values()):
        raise ValueError('Input BASE is not clean: populated lists found.')
    stage = next((target for target in project['targets'] if target.get('isStage')), None)
    if stage is None:
        raise ValueError('BASE is missing Stage.')
    preloaded = next((items[1] for items in stage.get('lists', {}).values()
                      if items[0] == 'Pre-loaded'), None)
    if preloaded is None:
        raise ValueError('Missing Pre-loaded list in BASE.sb3.')
    return project, len(preloaded)


def add_runtime_lists(sb3_path: Path, payload: dict[str, list],
                      scalar_payload: dict[str, object] | None = None):
    """Restore ROM-derived arrays and their matching runtime selectors."""
    with zipfile.ZipFile(sb3_path, 'r') as source:
        project = json.loads(source.read('project.json'))
        targets_by_list = defaultdict(list)
        for target in project.get('targets', []):
            for values in target.get('lists', {}).values():
                targets_by_list[values[0]].append(values)
        for name, values in payload.items():
            matches = targets_by_list.get(name, [])
            if len(matches) != 1:
                raise ValueError('Missing or duplicate ROM list in BASE: ' + name)
            if matches[0][1]:
                raise ValueError('Destination list already contains data: ' + name)
            if not isinstance(values, list) or not values:
                raise ValueError('Reconstruction produced an empty or malformed list: ' + name)
            matches[0][1].extend(values)

        if scalar_payload:
            stage = next((target for target in project.get('targets', [])
                          if target.get('isStage')), None)
            if stage is None:
                raise ValueError('Stage not found when updating the rig selector.')
            targets_by_variable = defaultdict(list)
            for target in project.get('targets', []):
                for values in target.get('variables', {}).values():
                    targets_by_variable[values[0]].append(values)
            for name, value in scalar_payload.items():
                matches = targets_by_variable.get(name, [])
                if len(matches) != 1:
                    raise ValueError('Missing or duplicate animation variable in BASE: ' + name)
                matches[0][1] = value

        payload_bytes = json.dumps(project, ensure_ascii=False,
                                   separators=(',', ':')).encode('utf-8')
        temporary = sb3_path.with_name(sb3_path.name + '.runtime.tmp')
        try:
            with zipfile.ZipFile(temporary, 'w') as target:
                for item in source.infolist():
                    _write_project(target, item, payload_bytes if item.filename == 'project.json'
                                   else source.read(item.filename))
            temporary.replace(sb3_path)
        finally:
            if temporary.exists():
                temporary.unlink()


def replace_title_background(sb3_path: Path, image: Image.Image):
    """Replace the clean BASE marker with the ROM-derived title backdrop."""
    buffer = BytesIO()
    image.convert('RGB').save(buffer, format='PNG', optimize=False)
    image_bytes = buffer.getvalue()
    asset_id = hashlib.md5(image_bytes).hexdigest()
    md5ext = asset_id + '.png'

    with zipfile.ZipFile(sb3_path, 'r') as source:
        project = json.loads(source.read('project.json'))
        stage = next((target for target in project.get('targets', [])
                      if target.get('isStage')), None)
        if stage is None:
            raise ValueError('Stage not found when replacing the intro background.')
        matches = [costume for costume in stage.get('costumes', [])
                   if costume.get('name') == 'Title Intro Background']
        if len(matches) != 1:
            raise ValueError('Missing or duplicate Title Intro Background placeholder.')
        costume = matches[0]
        costume['assetId'] = asset_id
        costume['md5ext'] = md5ext
        costume['dataFormat'] = 'png'
        costume['bitmapResolution'] = 1
        costume['rotationCenterX'] = 240
        costume['rotationCenterY'] = 180
        # BASE keeps the other title backdrops as transparent markers.  The
        # controller can legitimately visit one of them while the intro is
        # being initialised (the broadcasts are asynchronous in Scratch), so
        # point the two safe fallback backdrops at the same ROM image as well.
        # This does not add an image to the public package: all three names
        # share the one PNG just reconstructed above.
        for fallback_name in ('Title Intro Black', 'Plain Sky Fallback'):
            for fallback in stage.get('costumes', []):
                if fallback.get('name') == fallback_name:
                    fallback.update({'assetId': asset_id, 'md5ext': md5ext,
                                     'dataFormat': 'png', 'bitmapResolution': 1,
                                     'rotationCenterX': 240, 'rotationCenterY': 180})
        # BASE intentionally starts with a transparent fallback.  The title
        # controller normally selects the backdrop through broadcasts, but a
        # clean project can enter the head phase before those asynchronous
        # transition blocks have run.  Starting on the verified ROM backdrop
        # keeps the generated project visible and is harmless when the normal
        # phase switch subsequently runs.
        stage['currentCostume'] = next(
            index for index, item in enumerate(stage.get('costumes', []))
            if item.get('name') == 'Title Intro Background'
        )
        blocks = stage.setdefault('blocks', {})
        if 'porter_title_background_hat' not in blocks:
            blocks['porter_title_background_hat'] = {
                'opcode': 'event_whenflagclicked',
                'next': 'porter_title_background_switch',
                'parent': None,
                'inputs': {},
                'fields': {},
                'shadow': False,
                'topLevel': True,
                'x': 80,
                'y': 40,
            }
            blocks['porter_title_background_switch'] = {
                'opcode': 'looks_switchbackdropto',
                'next': None,
                'parent': 'porter_title_background_hat',
                'inputs': {'BACKDROP': [1, 'porter_title_background_value']},
                'fields': {},
                'shadow': False,
                'topLevel': False,
            }
            blocks['porter_title_background_value'] = {
                'opcode': 'looks_backdrops',
                'next': None,
                'parent': 'porter_title_background_switch',
                'inputs': {},
                'fields': {'BACKDROP': ['Title Intro Background', None]},
                'shadow': True,
                'topLevel': False,
            }
        payload = json.dumps(project, ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')
        referenced = {'project.json', md5ext}
        for target in project.get('targets', []):
            for asset in target.get('costumes', []) + target.get('sounds', []):
                if asset.get('md5ext'):
                    referenced.add(asset['md5ext'])
        temporary = sb3_path.with_name(sb3_path.name + '.title-background.tmp')
        try:
            with zipfile.ZipFile(temporary, 'w') as target:
                for item in source.infolist():
                    if item.filename not in referenced:
                        continue
                    if item.filename == 'project.json':
                        _write_project(target, item, payload)
                    elif item.filename == md5ext:
                        target.writestr(item, image_bytes)
                    else:
                        target.writestr(item, source.read(item.filename))
                if md5ext not in {item.filename for item in source.infolist()}:
                    target.writestr(md5ext, image_bytes)
            temporary.replace(sb3_path)
        finally:
            if temporary.exists():
                temporary.unlink()


def replace_title_effect_assets(sb3_path: Path,
                                assets_by_sprite: dict[str, list[tuple[str, Image.Image]]]):
    """Replace BASE's title effects and star-reveal markers with local assets."""
    encoded = {}
    for sprite, costumes in assets_by_sprite.items():
        for costume_name, image in costumes:
            buffer = BytesIO()
            image.convert('RGBA').save(buffer, format='PNG', optimize=False)
            image_bytes = buffer.getvalue()
            asset_id = hashlib.md5(image_bytes).hexdigest()
            encoded[(sprite, costume_name)] = (asset_id + '.png', image_bytes)

    with zipfile.ZipFile(sb3_path, 'r') as source:
        project = json.loads(source.read('project.json'))
        targets = {target.get('name'): target for target in project.get('targets', [])}
        for sprite, costumes in assets_by_sprite.items():
            target = targets.get(sprite)
            if target is None:
                raise ValueError('Missing effect sprite in BASE: ' + sprite)
            by_name = {costume.get('name'): costume for costume in target.get('costumes', [])}
            for costume_name, _image in costumes:
                costume = by_name.get(costume_name)
                if costume is None:
                    raise ValueError(f'Missing effect costume: {sprite}/{costume_name}')
                md5ext, _bytes = encoded[(sprite, costume_name)]
                costume.update({'assetId': md5ext[:-4], 'md5ext': md5ext,
                                'dataFormat': 'png'})

        payload = json.dumps(project, ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')
        referenced = {'project.json'}
        for target in project.get('targets', []):
            for asset in target.get('costumes', []) + target.get('sounds', []):
                if asset.get('md5ext'):
                    referenced.add(asset['md5ext'])
        bytes_by_name = {md5ext: image_bytes
                         for md5ext, image_bytes in encoded.values()}
        temporary = sb3_path.with_name(sb3_path.name + '.title-effects.tmp')
        try:
            with zipfile.ZipFile(temporary, 'w') as target:
                seen = set()
                for item in source.infolist():
                    if item.filename not in referenced:
                        continue
                    seen.add(item.filename)
                    if item.filename == 'project.json':
                        _write_project(target, item, payload)
                    elif item.filename in bytes_by_name:
                        target.writestr(item, bytes_by_name[item.filename])
                    else:
                        target.writestr(item, source.read(item.filename))
                for filename, image_bytes in bytes_by_name.items():
                    if filename not in seen:
                        target.writestr(filename, image_bytes)
            temporary.replace(sb3_path)
        finally:
            if temporary.exists():
                temporary.unlink()


def remove_base_notice(sb3_path: Path):
    """Remove the BASE-only onboarding overlay and restore the blank scene backdrop.

    The warning is a synchronizer safety guard, not part of the user's game
    logic. A locally rebuilt SB3 has passed the ROM/input checks, so keeping
    that green-flag script would cover the scene and prevent useful playtests.
    """
    with zipfile.ZipFile(sb3_path, 'r') as source:
        project = json.loads(source.read('project.json'))
        stage = next((target for target in project.get('targets', [])
                      if target.get('isStage')), None)
        if stage is None:
            raise ValueError('Stage not found when removing the BASE notice.')
        blocks = stage.get('blocks', {})
        for block_id in BASE_NOTICE_BLOCK_IDS:
            blocks.pop(block_id, None)
        # The distributed BASE currently points its green-flag hat at the
        # notice's switch-backdrop block. Restore the original clean fallback.
        for block in blocks.values():
            if block.get('next') in BASE_NOTICE_BLOCK_IDS:
                block['next'] = None
            for input_value in block.get('inputs', {}).values():
                if isinstance(input_value, list):
                    for index, item in enumerate(input_value):
                        if isinstance(item, str) and item in BASE_NOTICE_BLOCK_IDS:
                            input_value[index] = None
        notice_names = {'SM64 Scratch Base Notice'}
        stage['costumes'] = [costume for costume in stage.get('costumes', [])
                             if costume.get('name') not in notice_names]
        if not stage.get('costumes'):
            raise ValueError('Stage has no backdrops after removing the BASE notice.')
        stage['currentCostume'] = 0

        referenced = {'project.json'}
        for target in project.get('targets', []):
            for asset in target.get('costumes', []) + target.get('sounds', []):
                md5ext = asset.get('md5ext')
                if md5ext:
                    referenced.add(md5ext)
        payload = json.dumps(project, ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')
        temporary = sb3_path.with_name(sb3_path.name + '.notice.tmp')
        try:
            with zipfile.ZipFile(temporary, 'w') as target:
                for item in source.infolist():
                    if item.filename not in referenced:
                        continue
                    _write_project(target, item, payload if item.filename == 'project.json'
                                   else source.read(item.filename))
            temporary.replace(sb3_path)
        finally:
            if temporary.exists():
                temporary.unlink()


def apply_intro_interaction_lighting_fixes(sb3_path: Path):
    """Apply the small behavioural fixes that belong to the clean BASE.

    A backdrop click must not start the deformation pass: that pass restores
    its base vertices and would erase the current facial animation.  Only a
    successful grab refreshes LastDragFrame.  The hand sprite owns its actual
    visibility, including its appearance during the opening star reveal.
    The original Goddard picker accepts both axis distances below 20 units.
    The title renderer must select the head's ROM-derived surface normals
    through the actual loaded-model table.  Its former Object.Name lookup
    referred to a nonexistent list, bypassing the explicit head normal data.
    Both the white and red star lights contribute, as in the initial SB3.
    """
    with zipfile.ZipFile(sb3_path, 'r') as source:
        project = json.loads(source.read('project.json'))
        if project.get('extensionData', {}).get('intro_only_base', {}).get('version') == 1:
            from prepare_intro_base import validate_graph
            if project.get('extensionData', {}).get('v13_animation_grab_sparkles', {}).get('version') != 7:
                raise ValueError('Unsupported intro-only animation revision; regenerate the base.')
            if project.get('extensionData', {}).get('v13_goddard_camera', {}).get('version') != 5:
                raise ValueError('Unsupported intro-only camera revision; regenerate the base.')
            validate_graph(project)
            return {'intro_only_graph_preserved': True, 'patch_revision': 7}
        controller = next((target for target in project.get('targets', [])
                           if target.get('name') == 'Title Intro Controller'), None)
        from sm64_scratch.render_targets import intro_render_target
        render = intro_render_target(project)
        hand = next((target for target in project.get('targets', [])
                     if target.get('name') == 'Title Intro Goddard Hand'), None)
        if controller is None or render is None or hand is None:
            raise ValueError('Required intro sprites are missing; cannot apply fixes.')

        # Match goddard/draw_objects.c: ABS(cursor - grab) < 20 on both axes.
        controller_blocks = controller.get('blocks', {})
        picker_limits = 0
        for block_id in ('tic_titleintro_b_00249', 'tic_titleintro_b_00251'):
            block = controller_blocks.get(block_id)
            if not block or block.get('opcode') != 'operator_lt':
                raise ValueError(f'Missing grab selection block: {block_id}')
            operand = block.setdefault('inputs', {}).get('OPERAND2')
            if not isinstance(operand, list) or len(operand) < 2:
                raise ValueError(f'Invalid selection limit: {block_id}')
            operand[1] = [4, '20']
            picker_limits += 1

        # Move the existing timestamp assignment inside the successful-drag
        # branch, AFTER hit testing.  A mouse press alone is hand activity, not
        # a facial deformation.  Reuse the assignment so the patch is stable
        # when a generated project is processed a second time.
        cb = controller_blocks
        if cb['tic_titleintro_b_00071']['next'] == 'tic_titleintro_b_00222':
            cb['tic_titleintro_b_00071']['next'] = 'tic_titleintro_b_00226'
            cb['tic_titleintro_b_00226']['parent'] = 'tic_titleintro_b_00071'
            cb['tic_titleintro_b_00330']['next'] = 'tic_titleintro_b_00224'
            cb['tic_titleintro_b_00224']['parent'] = 'tic_titleintro_b_00330'
            del cb['tic_titleintro_b_00222'], cb['tic_titleintro_b_00223']
        elif (cb['tic_titleintro_b_00330']['next'] != 'tic_titleintro_b_00224'
              and not ('v13_goddard_camera_controls' in cb
                       and cb['tic_titleintro_b_00321']['next'] == 'tic_titleintro_b_00224')):
            raise ValueError('Unrecognized drag update graph.')

        # A background press must not hold the idle animator indefinitely.
        # Match the selected-grab condition used when entering its drag state.
        idle_condition = 'v13_input_idle_grab_and'
        cb[idle_condition] = {
            'opcode': 'operator_and', 'next': None,
            'parent': 'tic_titleintro_b_00609',
            'inputs': {'OPERAND1': [2, 'tic_titleintro_b_00610'],
                       'OPERAND2': [2, 'v13_input_idle_grab_gt']},
            'fields': {}, 'shadow': False, 'topLevel': False,
        }
        cb['v13_input_idle_grab_gt'] = {
            'opcode': 'operator_gt', 'next': None, 'parent': idle_condition,
            'inputs': {'OPERAND1': [3, [12, 'TitleIntro.SelectedGrab',
                                       'titleintro_var_004'], [10, '0']],
                       'OPERAND2': [1, [4, '0']]},
            'fields': {}, 'shadow': False, 'topLevel': False,
        }
        cb['tic_titleintro_b_00609']['inputs']['OPERAND'] = [2, idle_condition]
        cb['tic_titleintro_b_00610']['parent'] = idle_condition

        # The controller used LastDragFrame for visibility, while the actual
        # hand has its own mouse-activity timer.  These disagree at startup
        # (visible hand, silent appearance) and whenever the mouse merely moves.
        # Remove the obsolete visibility branches and publish the real state
        # from the sprite's show/hide paths instead.
        if cb['tic_titleintro_b_00339']['next'] == 'tic_titleintro_b_00346':
            cb['tic_titleintro_b_00339']['next'] = 'tic_titleintro_b_00359'
            cb['tic_titleintro_b_00359']['parent'] = 'tic_titleintro_b_00339'
            for number in range(346, 359):
                del cb[f'tic_titleintro_b_{number:05d}']
        elif cb['tic_titleintro_b_00339']['next'] != 'tic_titleintro_b_00359':
            raise ValueError('Unrecognized hand visibility graph.')
        hb = hand['blocks']
        for block_id, value in (
                ('v20_hand_hide0', 0), ('v20_hand_show', 1),
                ('v20_hand_hide', 0), ('v20_hand_hideend', 0),
                ('titleintro_hand_00025', 0), ('v28_hand_flag_hide', 0)):
            if block_id not in hb:
                raise ValueError(f'Missing hand visibility block: {block_id}')
            state_id = 'v13_input_visibility_' + block_id
            following = hb[block_id]['next']
            if following != state_id:
                hb[block_id]['next'] = state_id
            else:
                following = hb[state_id]['next']
            hb[state_id] = {
                'opcode': 'data_setvariableto', 'next': following,
                'parent': block_id, 'inputs': {'VALUE': [1, [4, str(value)]]},
                'fields': {'VARIABLE': ['TitleIntro.HandVisible', 'titleintro_var_023']},
                'shadow': False, 'topLevel': False,
            }
            if following:
                hb[following]['parent'] = state_id

        # Object.Name is not a defined list in this engine.  Use the same
        # loaded-model lookup as the title shade selector so the head uses
        # its explicitly reconstructed outward surface normals.
        render_blocks = render.get('blocks', {})
        normal_item = render_blocks.get('v17_title_normal_nameitem')
        if not normal_item or normal_item.get('opcode') != 'data_itemoflist':
            raise ValueError('Missing head normal selection block in Render.')
        normal_index = 'v13_head_normal_model_index'
        normal_item['inputs']['INDEX'] = [3, normal_index, [10, '']]
        normal_item['fields']['LIST'] = ['Loaded models', 'DBINywvWyrFFDF4eN}y`']
        render_blocks[normal_index] = {
            'opcode': 'operator_add', 'next': None,
            'parent': 'v17_title_normal_nameitem',
            'inputs': {'NUM1': [3, [12, 'engine.modelindex', '}g*3Vg|VS$4N)Gw1htzL'], [10, '']],
                       'NUM2': [1, [4, '1']]},
            'fields': {}, 'shadow': False, 'topLevel': False,
        }

        # Keep the red star contribution during its visible passes. The
        # Scratch intro holds its light tracks after frame 660, unlike the C
        # animation: leaving that held red direction active tinted unrelated
        # brow/cheek surfaces during idle poses. Gate that project adaptation
        # with the same ROM-derived visibility track used by the red sprite.
        red_set = render_blocks.get('v15_red_dot_set')
        if not red_set or red_set.get('opcode') != 'data_setvariableto':
            raise ValueError('Missing red lighting block in Render.')
        red_set.setdefault('inputs', {})['VALUE'] = [3, 'v15_rdot_add1', [10, '0']]
        red_gate = 'v13_red_visibility_if'
        following = red_set['next']
        if following == red_gate:
            following = render_blocks[red_gate]['next']
        stage = next(target for target in project['targets'] if target.get('isStage'))
        red_visible = next(key for key, value in stage['lists'].items()
                           if value[0] == 'TitleIntro.RedStarVisible')
        frame_id = next(key for key, value in stage['variables'].items()
                        if value[0] == 'TitleIntro.AnimFrame')
        def rb(opcode, parent, inputs=None, fields=None, next_block=None):
            return dict(opcode=opcode, next=next_block, parent=parent,
                        inputs=inputs or {}, fields=fields or {}, shadow=False, topLevel=False)
        render_blocks[red_gate] = rb('control_if', 'v15_red_dot_set',
            {'CONDITION':[2,'v13_red_visibility_not'], 'SUBSTACK':[2,'v13_red_visibility_zero']},
            next_block=following)
        render_blocks['v13_red_visibility_not'] = rb('operator_not',red_gate,
            {'OPERAND':[2,'v13_red_visibility_eq']})
        render_blocks['v13_red_visibility_eq'] = rb('operator_equals','v13_red_visibility_not',
            {'OPERAND1':[3,'v13_red_visibility_item',[10,'0']], 'OPERAND2':[1,[4,'1']]})
        render_blocks['v13_red_visibility_item'] = rb('data_itemoflist','v13_red_visibility_eq',
            {'INDEX':[3,[12,'TitleIntro.AnimFrame',frame_id],[10,'0']]},
            {'LIST':['TitleIntro.RedStarVisible',red_visible]})
        render_blocks['v13_red_visibility_zero'] = rb('data_setvariableto',red_gate,
            {'VALUE':[1,[4,'0']]}, {'VARIABLE':['Rig.RedDot','v15_var_red_dot']})
        red_set['next'] = red_gate
        if following:
            render_blocks[following]['parent'] = red_gate

        # Constant UVs at the first row of a shade band can interpolate just
        # below that boundary in the software rasterizer, sampling the red
        # page above it. Use the centre of the uniform 128-row band instead.
        # This changes no palette colour or light intensity.
        render_blocks['v13_head_shade_center'] = rb('operator_add', 'v15_offset_set',
            {'NUM1':[3,'v15_offset_mul128',[10,'0']], 'NUM2':[1,[4,'64']]})
        render_blocks['v15_offset_set']['inputs']['VALUE'] = [3,'v13_head_shade_center',[10,'0']]
        render_blocks['v15_offset_mul128']['parent'] = 'v13_head_shade_center'

        project.setdefault('extensionData', {})['v13_intro_fixes'] = {
            'grab_picker_half_extent': 20,
            'title_head_red_light_bucket': 'visible_red_star_passes_only',
            'title_head_shade_sample': 'centre_of_uniform_128_row_band',
            'title_head_normal_model_lookup': 'Loaded models[engine.modelindex + 1]',
            'title_head_lighting_normals': 'outward_runtime_face_normals',
            'deformation_timestamp': 'successful_grab_only',
            'hand_visibility_source': 'actual_hand_sprite',
            'blank_click_keeps_facial_animation': True,
        }
        from sm64_scratch.patches.patch_animation import apply_animation_fixes
        animation_report = apply_animation_fixes(project)
        payload = json.dumps(project, ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')
        temporary = sb3_path.with_name(sb3_path.name + '.introfix.tmp')
        try:
            with zipfile.ZipFile(temporary, 'w') as target:
                for item in source.infolist():
                    _write_project(target, item,
                                   payload if item.filename == 'project.json'
                                   else source.read(item.filename))
            temporary.replace(sb3_path)
        finally:
            if temporary.exists():
                temporary.unlink()
    return {
        'animation_grab_sparkles': animation_report,
        'camera_controls': project.get('extensionData', {}).get('v13_goddard_camera', {}),
        'grab_picker_half_extent': 20,
        'grab_picker_limits_patched': picker_limits,
        'title_head_red_light_bucket': 'visible_red_star_passes_only',
        'title_head_shade_sample': 'centre_of_uniform_128_row_band',
        'title_head_normal_model_lookup': 'Loaded models[engine.modelindex + 1]',
        'title_head_lighting_normals': 'outward_runtime_face_normals',
        'deformation_timestamp': 'successful_grab_only',
        'hand_visibility_source': 'actual_hand_sprite',
        'blank_click_keeps_facial_animation': True,
    }


def add_asset_models(sb3_path, by_model, restored_models):
    """Insert the title logo and head; both already contain their ROM textures."""
    if by_model:
        raise ValueError("Separate texture injection is outside this intro builder")
    with zipfile.ZipFile(sb3_path) as source:
        project = json.loads(source.read('project.json'))
        stage = next(t for t in project['targets'] if t.get('isStage'))
        preloaded = next(v for v in stage['lists'].values() if v[0] == 'Pre-loaded')
        if preloaded[1]:
            raise ValueError("Pre-loaded must be empty in the input base")
        seen = set()
        for name, model, report in sorted(restored_models):
            if name in seen:
                raise ValueError("Duplicate model: " + name)
            seen.add(name)
            preloaded[1].extend(('+' + name, model))
        payload = json.dumps(project, separators=(',', ':')).encode()
        temporary = sb3_path.with_name(sb3_path.name + '.models.tmp')
        try:
            with zipfile.ZipFile(temporary, 'w') as result:
                for item in source.infolist():
                    _write_project(result, item, payload if item.filename == 'project.json' else source.read(item.filename))
            temporary.replace(sb3_path)
        finally:
            temporary.unlink(missing_ok=True)
