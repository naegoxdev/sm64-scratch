#!/usr/bin/env python3
"""Update distributable, assetless BASE from a PRIVATE evolving SB3.

Copies Scratch logic (blocks, variables, comments, metadata, broadcast definitions),
not the private media bytes and not any list values. Existing BASE placeholder
media is reused; newly introduced costumes get generated transparent placeholders.
Serialized asset values, list monitor values and unreferenced BASE files are also
removed. This is a development tool, NOT a complete ROM->SB3 constructor.
"""
from __future__ import annotations
from sm64_scratch.paths import BASE_PATH
import argparse
import copy
import hashlib
import json
import re
import struct
import sys
import zlib
import zipfile
from collections import Counter
from pathlib import Path

NOTICE_IDS = {'notice_greenflag', 'notice_switchbackdrop'}
PNG_SIG = bytes.fromhex('89504e470d0a1a0a')
ASSET_COMMAND = re.compile(r'\b(?:newtex|newgeo|newmat|newnode)\b', re.IGNORECASE)


def contains_asset_payload(value):
    """Detect obvious serialized model or texture payloads in variable values."""
    if isinstance(value, str):
        return len(value) > 2048 or bool(ASSET_COMMAND.search(value))
    if isinstance(value, dict):
        return len(value) > 2048 or any(contains_asset_payload(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return len(value) > 2048 or any(contains_asset_payload(v) for v in value)
    return False


def safe_variable_default(value):
    """Use a harmless Scratch default when a new variable carries asset data."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return 0
    return ''


def make_png_placeholder() -> bytes:
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return PNG_SIG + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\x00\x00\x00\x00')) + chunk(b'IEND', b'')


def media_stub(ext: str) -> bytes:
    ext = ext.lower()
    if ext == 'png':
        return make_png_placeholder()
    if ext == 'svg':
        return b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1" viewBox="0 0 1 1"></svg>'
    raise ValueError('Unsupported placeholder costume format: '+ext)


def read_project(path: Path):
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad:
            raise ValueError('Corrupt ZIP: ' + str(bad))
        project = json.loads(z.read('project.json'))
        names = set(z.namelist())
        return project, names


def target_key(t):
    return ('STAGE' if t.get('isStage') else 'SPRITE', t['name'])


def _block_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _block_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _block_strings(item)


def synchronize(base_path: Path, private_path: Path, output_path: Path, report_path=None, gameplay_only=False):
    paths = [p.resolve() for p in (base_path, private_path, output_path)]
    if len(set(paths)) != 3:
        raise ValueError('BASE, private project and output must be different files.')
    if report_path and report_path.resolve() in set(paths):
        raise ValueError('Report must not overwrite an input or output project.')
    base, base_files = read_project(base_path)
    if base.get('extensionData', {}).get('intro_only_base'):
        raise ValueError('Synchronize BASE_FULL first, then regenerate the intro-only base.')
    private, _ = read_project(private_path)
    merge_report = None
    if gameplay_only:
        from sm64_scratch.gameplay_merge import merge_gameplay_with_intro
        private, merge_report = merge_gameplay_with_intro(base, private)
    refs = {target_key(t): t for t in base['targets']}
    if len(refs) != len(base['targets']):
        raise ValueError('BASE contains duplicate sprite names.')
    if len({target_key(t) for t in private['targets']}) != len(private['targets']):
        raise ValueError('Private project contains duplicate sprite names.')
    if ('STAGE', 'Stage') not in refs:
        raise ValueError('BASE is missing Stage.')
    if any(v[1] for t in base['targets'] for v in t.get('lists', {}).values()):
        raise ValueError('Input BASE is not clean: nonempty lists found.')

    media_additions = {}
    counts = Counter()
    asset_manifest = []
    targets = []
    from sm64_scratch.patches.patch_game_features import PORTER_OWNED_SPRITES
    for src in private['targets']:
        if src['name'] in PORTER_OWNED_SPRITES:
            counts['porter_owned_sprites_regenerated_by_builder'] += 1
            continue
        key = target_key(src)
        old = refs.get(key)
        if old is None:
            counts['new_sprites'] += 1
        out = copy.deepcopy(src)
        # Variable definitions evolve with the logic, but large serialized
        # model/texture values are local assets and must not enter the BASE.
        base_variables = old.get('variables', {}) if old else {}
        for variable_id, definition in out.get('variables', {}).items():
            if len(definition) < 2 or not contains_asset_payload(definition[1]):
                continue
            counts['private_variable_payloads_removed'] += 1
            base_definition = base_variables.get(variable_id)
            if (base_definition and len(base_definition) > 1
                    and not contains_asset_payload(base_definition[1])):
                definition[1] = copy.deepcopy(base_definition[1])
                counts['payload_variables_reset_to_base'] += 1
            else:
                definition[1] = safe_variable_default(definition[1])
                counts['payload_variables_reset_to_default'] += 1
        # List IDs/names are part of the program. List data, including ROM art,
        # mesh, atlas, action clips and runtime buffers, are NEVER copied.
        out['lists'] = {list_id: [definition[0], []]
                        for list_id, definition in src.get('lists', {}).items()}
        counts['list_definitions'] += len(out['lists'])
        counts['discarded_private_list_entries'] += sum(len(x[1]) for x in src.get('lists', {}).values())
        counts['nonempty_lists_to_rebuild'] += sum(bool(x[1]) for x in src.get('lists', {}).values())
        # Scratch sprite blocks are the user's evolving logic. Keep them, but
        # leave the BASE safety notice until the full builder really exists.
        if src.get('isStage') and old:
            notice = {name: copy.deepcopy(block) for name, block in old['blocks'].items() if name in NOTICE_IDS}
            collision = set(notice) & set(out['blocks'])
            if collision:
                raise ValueError('Reserved notice IDs in Stage: ' + str(collision))
            out['blocks'].update(notice)
        counts['logic_blocks'] += len(src.get('blocks', {}))
        # Sprite costumes always come from BASE placeholders, never PRIVATE media.
        old_costumes = {c['name']: c for c in old.get('costumes', [])} if old else {}
        rebuilt = []
        for index, costume in enumerate(src.get('costumes', [])):
            c = copy.deepcopy(costume)
            original_md5ext = c.get('md5ext')
            fmt = c.get('dataFormat', '').lower()
            reference = old_costumes.get(c['name'])
            if reference and reference.get('dataFormat','').lower() == fmt and reference.get('md5ext') in base_files:
                stub_md5ext = reference['md5ext']
                counts['reused_placeholders'] += 1
                if original_md5ext != stub_md5ext:
                    counts['changed_source_media_not_exported'] += 1
            else:
                stub = media_stub(fmt)
                digest = hashlib.md5(stub).hexdigest()
                stub_md5ext = digest + '.' + fmt
                media_additions[stub_md5ext] = stub
                counts['new_placeholders'] += 1
            c['md5ext'] = stub_md5ext
            c['assetId'] = stub_md5ext.rsplit('.', 1)[0]
            rebuilt.append(c)
            asset_manifest.append({'target':src['name'], 'costume_index':index, 'costume':c['name'],
                                   'source_media_id':original_md5ext, 'placeholder_media_id':stub_md5ext,
                                   'requires_real_media_reconstruction': True})
        out['costumes'] = rebuilt
        # Known title audio is rebuilt by the ROM constructor. Preserve its
        # blocks and omit its media records/bytes from the clean BASE.
        from sm64_scratch.decoders.decode_audio import TITLE_AUDIO_DISPLAY_NAMES
        from sm64_scratch.decoders.decode_game_audio import GAME_AUDIO_DISPLAY_NAMES
        rebuilt_audio_names = set(TITLE_AUDIO_DISPLAY_NAMES.values()) | set(GAME_AUDIO_DISPLAY_NAMES.values())
        old_sound_media = {s['md5ext'] for s in old.get('sounds', [])} if old else set()
        kept_sounds = []
        for snd in src.get('sounds', []):
            if src.get('isStage') and snd.get('name') in rebuilt_audio_names:
                counts['rom_sound_records_to_rebuild'] += 1
                continue
            if snd['md5ext'] not in old_sound_media or snd['md5ext'] not in base_files:
                raise ValueError('New sound without a reconstruction recipe: %s / %s. '
                                 'Its private audio was not copied.' % (src['name'], snd['name']))
            kept_sounds.append(copy.deepcopy(snd))
        out['sounds'] = kept_sounds
        targets.append(out)
    base['targets'] = targets
    # Project-level editor data and extensions can evolve with the logic.
    for field in ('extensions','extensionURLs','monitors','tempo','videoTransparency','videoState','textToSpeechLanguage'):
        if field in private:
            value = copy.deepcopy(private[field])
            if field == 'monitors' and isinstance(value, list):
                private_list_ids = {list_id for target in private['targets']
                                    for list_id in target.get('lists', {})}
                safe_variables = {variable_id: definition[1]
                                  for target in base['targets']
                                  for variable_id, definition in target.get('variables', {}).items()
                                  if len(definition) > 1}
                for monitor in value:
                    if not isinstance(monitor, dict):
                        continue
                    if monitor.get('mode') == 'list' or monitor.get('id') in private_list_ids:
                        monitor['value'] = []
                        counts['list_monitor_values_emptied'] += 1
                    elif 'value' in monitor and contains_asset_payload(monitor['value']):
                        monitor['value'] = copy.deepcopy(
                            safe_variables.get(monitor.get('id'), safe_variable_default(monitor['value'])))
                        counts['private_monitor_payloads_removed'] += 1
            base[field] = value
    if any(len(value) > 2048 for target in base['targets']
           for value in _block_strings(target.get('blocks', {}))):
        raise ValueError('Possible private payload embedded in blocks; review before synchronization.')
    # Reapply the porter-owned intro logic after private block updates. The
    # patch adds only empty caches and reuses an existing BASE placeholder.
    # Generic projects without the Goddard controller retain the v11 workflow.
    if any(t['name'] == 'Title Intro Controller' for t in base['targets']):
        from sm64_scratch.patches.patch_animation import apply_animation_fixes
        intro_fixes = apply_animation_fixes(base)
    else:
        intro_fixes = None
    from sm64_scratch.patches.patch_scene import apply_scene_lookup_guards
    apply_scene_lookup_guards(base)
    has_pipe_migration = bool(base.get('extensionData', {}).get('pipe_intro_camera_fix'))
    if (has_pipe_migration
            and not any(b.get('mutation', {}).get('proccode') == 'INTRO | inherit pipe camera into gameplay'
                        for t in base['targets'] if t['name'] == 'Mario' for b in t['blocks'].values())):
        # The source block graph replaces the old graph during synchronization.
        # Drop the graph-specific marker so the preserved migration is applied.
        base['extensionData'].pop('pipe_intro_camera_fix')
    if has_pipe_migration:
        from sm64_scratch.patches.patch_pipe_camera import apply_pipe_camera_fixes
        apply_pipe_camera_fixes(base)
        from sm64_scratch.patches.patch_arrival import apply_arrival_fixes
        apply_arrival_fixes(base)
        from sm64_scratch.patches.patch_frame_pipeline import apply_frame_pipeline
        apply_frame_pipeline(base)
        from sm64_scratch.patches.patch_game_presentation import apply_game_presentation
        # A new source replaces these graphs; graph-specific migration markers
        # must not prevent regenerating the render and input hooks.
        if not any(b.get('mutation', {}).get('proccode') == 'INTRO | frame pipe rise from below'
                   for t in base['targets'] if t['name'] == 'Mario' for b in t['blocks'].values()):
            base['extensionData'].pop('game_presentation_fix', None)
        apply_game_presentation(base)
        from sm64_scratch.patches.patch_arrival_flight import apply_arrival_flight
        apply_arrival_flight(base)
        from sm64_scratch.patches.patch_landing import apply_landing_fixes
        apply_landing_fixes(base)
        from sm64_scratch.patches.patch_keyboard import apply_keyboard_running
        apply_keyboard_running(base)
        from sm64_scratch.patches.patch_gameplay_v13 import apply_gameplay_fixes
        apply_gameplay_fixes(base)
        from sm64_scratch.patches.patch_damage_camera_hud import apply_damage_camera_hud
        apply_damage_camera_hud(base)
        from sm64_scratch.patches.patch_beta010 import apply_beta010
        apply_beta010(base)
        from sm64_scratch.patches.patch_bob_objectives import apply_bob_objectives
        apply_bob_objectives(base)
        from sm64_scratch.patches.patch_red_coin_feedback import apply_red_coin_feedback
        apply_red_coin_feedback(base)
        from sm64_scratch.patches.patch_runtime_stability import apply_runtime_stability
        apply_runtime_stability(base)
        from sm64_scratch.patches.patch_bob_expansion import apply_bob_expansion
        apply_bob_expansion(base)
        from sm64_scratch.patches.patch_cannon_view import apply_cannon_view
        apply_cannon_view(base)
        from sm64_scratch.patches.patch_locked_cap_boxes import apply_locked_cap_boxes
        apply_locked_cap_boxes(base)
    # Enforce no list values and that all media references exist in BASE.
    if any(x[1] for t in base['targets'] for x in t.get('lists', {}).values()):
        raise AssertionError('Data leak: nonempty list in BASE.')
    referenced_media = {asset['md5ext'] for target in base['targets']
                        for asset in target.get('costumes', []) + target.get('sounds', [])}
    available = base_files | set(media_additions)
    for target in base['targets']:
        for asset in target.get('costumes', []) + target.get('sounds', []):
            if asset['md5ext'] not in available:
                raise ValueError('Missing file after update: '+asset['md5ext'])
    counts['unreferenced_base_media_removed'] = len((base_files - {'project.json'}) - referenced_media)
    serialized = json.dumps(base, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(base_path) as src, zipfile.ZipFile(output_path, 'w') as dst:
        for item in src.infolist():
            if item.filename == 'project.json':
                dst.writestr(item, serialized)
            elif item.filename in referenced_media:
                dst.writestr(item, src.read(item.filename))
        for filename, data in media_additions.items():
            if filename not in base_files:
                dst.writestr(filename, data, compress_type=zipfile.ZIP_DEFLATED)
    result = {'format':11, 'status':'ASSETLESS_BASE_LOGIC_ONLY_INCOMPLETE_GAME',
              'counts':dict(counts), 'costumes':asset_manifest,
              'gameplay_merge': merge_report,
              'intro_animation_fixes': intro_fixes,
              'note':'No copied private costume/sound bytes or list contents. '
                     'ROM geometry, animations, collisions, HUD and other media still need generators.'}
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result


def main():
    p = argparse.ArgumentParser(description='Update the public base from private logic without copying media or list values.')
    p.add_argument('--full', type=Path, required=True, help='Your updated private SB3. Never include it in the public package.')
    p.add_argument('--base', type=Path, default=BASE_PATH)
    p.add_argument('-o', '--output', type=Path, default=Path('local_build/BASE_UPDATED.sb3'))
    p.add_argument('--report', type=Path, default=Path('local_build/sync_report.json'))
    p.add_argument('--gameplay-only', action='store_true', help='Retain the existing intro when the private update contains gameplay only')
    a = p.parse_args()
    try:
        result = synchronize(a.base, a.full, a.output, a.report, a.gameplay_only)
        print('[+] Updated logic base:', a.output)
        print('[+] Pending costume report:', a.report)
        print(json.dumps(result['counts'], ensure_ascii=False, indent=2))
        print('[!] Lists remain empty. This base is not playable and contains no private artwork.')
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as e:
        print('[-] ERROR:', e, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
