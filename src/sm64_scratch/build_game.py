#!/usr/bin/env python3
"""Phase 2 full-game development builder: only the local ROM and clean BASE.

This is an incomplete reconstruction, not a full-game release. The report
separates restored geometry from texture-only records and missing resources.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

from sm64_scratch.rom_codec import normalized_rom, ROM_SHA1
from sm64_scratch.decoders.decode_assets import decode_verified_assets
from sm64_scratch.decoders.decode_models import extract_models
from sm64_scratch.decoders.decode_grounds import build_castle_grounds_model
from sm64_scratch.decoders.decode_garden_runtime import build_garden_runtime, build_tree_model, build_pipe_model
from sm64_scratch.decoders.decode_doors import build_door_models, build_door_animation_payload
from sm64_scratch.decoders.decode_goombas import build_goomba_models, build_goomba_runtime
from sm64_scratch.decoders.decode_garden_actors import build_butterfly_model, build_bird_model, build_flag_model, build_fish_model, build_garden_actor_runtime
from sm64_scratch.decoders.decode_toad import build_toad_model, build_toad_animation_payload
from sm64_scratch.decoders.decode_painting import build_painting_model
from sm64_scratch.decoders.decode_shadow import build_shadow_model
from sm64_scratch.decoders.decode_static_actors import build_static_actor_models, build_chomp_animation_payload, build_buddy_animation_payload, build_battlefield_tree_payload
from sm64_scratch.decoders.decode_coins import build_coin_models, build_coin_placement_payload
from sm64_scratch.decoders.decode_scenes import build_scene_model
from sm64_scratch.decoders.decode_mario import build_mario_model
from sm64_scratch.decoders.decode_mario_poses import build_mario_animation_payload, build_mario_gameplay_payload
from sm64_scratch.decoders.decode_collisions import build_collision_payload
from sm64_scratch.decoders.decode_sky import build_sky_texture_payload
from sm64_scratch.decoders.decode_hud import build_hud_assets
from sm64_scratch.decoders.decode_transitions import build_game_transition_assets
from sm64_scratch.decoders.decode_head import build_head_model, build_title_runtime_payload
from sm64_scratch.decoders.decode_intro import (build_logo_scale_payload, build_head_animation_payload,
                          build_star_animation_payload, build_title_effect_assets,
                          build_title_star_reveal_assets)
from sm64_scratch.decoders.decode_background import build_title_background, build_title_background_runtime_payload
from sm64_scratch.decoders.decode_audio import build_audio_assets
from sm64_scratch.decoders.decode_game_audio import build_game_audio_assets
from sm64_scratch.patches.patch_game_features import apply_game_features, PORT_CREDIT
from sm64_scratch.sb3_builder import (inspect_clean_base, add_runtime_lists, remove_base_notice,
                         apply_intro_interaction_lighting_fixes, replace_title_background,
                         replace_title_effect_assets, add_audio_assets, _write_project)
from sm64_scratch.game_builder import add_game_models, replace_costume_assets, compact_archive
from sm64_scratch.runtime_settings import ensure_intro_runtime_settings

from sm64_scratch.paths import BASE_PATH, METADATA_DIR, OUTPUT_DIR


def metadata(name):
    doc = json.loads((METADATA_DIR / (name + '.json')).read_text())
    if doc.get('format') != 13 or doc.get('rom_sha1', ROM_SHA1) != ROM_SHA1:
        raise ValueError('Invalid metadata format or ROM identifier: ' + name)
    return doc


def _diagnostic_audio_assets():
    """Keep all sound names for logic checks; never imply audible fidelity."""
    from sm64_scratch.decoders.decode_audio import TITLE_AUDIO_DISPLAY_NAMES
    from sm64_scratch.decoders.decode_game_audio import GAME_AUDIO_DISPLAY_NAMES
    from io import BytesIO
    import wave

    out = BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(32000)
        wav.writeframes(b'\x00\x00\x00\x00' * 1600)
    silence = out.getvalue()
    return {key: (silence, {'verification_silence': True})
            for key in {**TITLE_AUDIO_DISPLAY_NAMES, **GAME_AUDIO_DISPLAY_NAMES}}


def main(argv=None, *, diagnostic_default=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, required=True, help='User-owned US ROM')
    parser.add_argument('--base', type=Path, default=BASE_PATH, help='Assetless full-game BASE')
    parser.add_argument('--out', type=Path, help='Generated SB3 (default: output/MARIO64_SCRATCH.sb3)')
    parser.add_argument('--report', type=Path, help='Build report (default: output/build_report.json)')
    parser.add_argument('--credit', default=PORT_CREDIT, help='Author credit displayed during the logo')
    parser.add_argument('--diagnostic-silent-audio', action='store_true', default=diagnostic_default,
                        help='Logic/packaging checks only: replace ROM audio with short silence')
    args = parser.parse_args(argv)
    if args.out is None:
        filename = 'MARIO64_SCRATCH_DIAGNOSTIC_SILENT.sb3' if args.diagnostic_silent_audio else 'MARIO64_SCRATCH.sb3'
        args.out = OUTPUT_DIR / filename
    if args.report is None:
        filename = 'diagnostic_report.json' if args.diagnostic_silent_audio else 'build_report.json'
        args.report = OUTPUT_DIR / filename
    paths = [p.resolve() for p in (args.rom, args.base, args.out, args.report)]
    if len(set(paths)) != len(paths):
        parser.error('ROM, BASE, output and report must be different files')
    rom = normalized_rom(args.rom.read_bytes())
    if hashlib.sha1(rom).hexdigest() != ROM_SHA1:
        parser.error('Unsupported ROM; expected normalized US SHA-1: ' + ROM_SHA1)
    base, _ = inspect_clean_base(args.base)
    if base.get('extensionData', {}).get('intro_only_base'):
        parser.error('Phase 2 requires the full-game BASE, not the reduced intro BASE')
    ensure_intro_runtime_settings(base)
    from sm64_scratch.patches.patch_pipe_camera import apply_pipe_camera_fixes
    pipe_camera_fixes = apply_pipe_camera_fixes(base)
    from sm64_scratch.patches.patch_arrival import apply_arrival_fixes
    arrival_fixes = apply_arrival_fixes(base)
    from sm64_scratch.patches.patch_frame_pipeline import apply_frame_pipeline
    frame_pipeline = apply_frame_pipeline(base)
    from sm64_scratch.patches.patch_game_presentation import apply_game_presentation
    presentation_fixes = apply_game_presentation(base)
    from sm64_scratch.patches.patch_arrival_flight import apply_arrival_flight
    flight_fixes = apply_arrival_flight(base)
    from sm64_scratch.patches.patch_landing import apply_landing_fixes
    apply_landing_fixes(base)
    from sm64_scratch.patches.patch_keyboard import apply_keyboard_running
    keyboard_running = apply_keyboard_running(base)
    from sm64_scratch.patches.patch_gameplay_v13 import apply_gameplay_fixes
    gameplay_fixes = apply_gameplay_fixes(base)
    from sm64_scratch.patches.patch_damage_camera_hud import apply_damage_camera_hud
    damage_camera_hud = apply_damage_camera_hud(base)
    from sm64_scratch.patches.patch_beta010 import apply_beta010
    beta010 = apply_beta010(base)
    from sm64_scratch.patches.patch_course_hud_waterfall import apply_course_hud_waterfall
    course_hud_waterfall = apply_course_hud_waterfall(base)
    from sm64_scratch.patches.patch_bob_objectives import apply_bob_objectives, build_cannon_reticle_assets
    bob_objectives = apply_bob_objectives(base)
    from sm64_scratch.patches.patch_red_coin_feedback import apply_red_coin_feedback
    red_coin_feedback = apply_red_coin_feedback(base)
    from sm64_scratch.patches.patch_runtime_stability import apply_runtime_stability
    runtime_stability = apply_runtime_stability(base)
    from sm64_scratch.patches.patch_bob_expansion import apply_bob_expansion, build_bob_overlay_assets
    bob_expansion = apply_bob_expansion(base)
    from sm64_scratch.patches.patch_cannon_view import apply_cannon_view
    cannon_view = apply_cannon_view(base)
    from sm64_scratch.patches.patch_locked_cap_boxes import apply_locked_cap_boxes
    locked_cap_boxes = apply_locked_cap_boxes(base)
    from sm64_scratch.patches.patch_scene import apply_scene_lookup_guards
    scene_fixes = apply_scene_lookup_guards(base)

    # Release fixes validated during the final integration pass. These edit
    # only Scratch logic; no ROM-derived asset bytes are bundled here.
    from sm64_scratch.patches.patch_bob_entry_stall import apply_bob_entry_stall_fix
    bob_entry_stall = apply_bob_entry_stall_fix(base)
    from sm64_scratch.patches.patch_fast_scene_create import apply_fast_scene_create
    fast_scene_create = apply_fast_scene_create(base)
    from sm64_scratch.patches.patch_castle_door_decomp_rewrite import apply_castle_door_decomp_rewrite
    apply_castle_door_decomp_rewrite(base)
    from sm64_scratch.patches.patch_castle_camera_grip_v2 import apply_castle_camera_grip_v2
    castle_camera_grip_v2 = apply_castle_camera_grip_v2(base)
    from sm64_scratch.patches.patch_bob_signs import apply_bob_sign_fixes
    bob_sign_fixes = apply_bob_sign_fixes(base)
    from sm64_scratch.patches.patch_dive_rollout_anim import apply_dive_rollout_animation
    dive_rollout_animation = apply_dive_rollout_animation(base)
    from sm64_scratch.patches.patch_ledge_grab import apply_ledge_grab
    ledge_grab = apply_ledge_grab(base)
    from sm64_scratch.patches.patch_triple_jump_chain import apply_triple_jump_chain_fix
    triple_jump_chain = apply_triple_jump_chain_fix(base)
    from sm64_scratch.patches.patch_red_coin_shadows import apply_red_coin_shadows
    red_coin_shadows = apply_red_coin_shadows(base)
    # Restore the star camera and exit wipes after the newer coin-shadow patch.
    from sm64_scratch.patches.patch_native_star_exit import apply_native_star_exit
    native_star_exit = apply_native_star_exit(base)
    print('[1/5] Decoding verified textures and costumes...', flush=True)
    by_model, costumes, hashes, asset_report = decode_verified_assets(
        rom, metadata('rom_layout'), metadata('texture_recipes'), metadata('costume_recipes'))
    print('[2/5] Decoding scenes, Mario and the title head...', flush=True)
    restored = extract_models(rom, metadata('models'), by_model)
    restored.append(build_castle_grounds_model(rom, verified_textures=by_model.get('Arena', [])))
    from sm64_scratch.decoders.decode_arrival_flight import (build_arrival_lakitu_model, build_arrival_cloud_model,
                                       build_arrival_flight_payload)
    lakitu = build_arrival_lakitu_model(rom)
    restored.extend([lakitu, build_arrival_cloud_model(rom)])
    restored.append(build_tree_model(rom))
    restored.append(build_pipe_model(rom, by_model['Pipe']))
    restored.extend(build_door_models(rom, by_model))
    restored.extend(build_coin_models(rom, by_model))
    from sm64_scratch.decoders.decode_bob_objectives import build_red_coin_models, build_bob_objective_payload, build_red_coin_number_models
    restored.extend(build_red_coin_models(rom))
    restored.extend(build_red_coin_number_models(rom))
    restored.extend(build_goomba_models(rom, by_model))
    restored.extend(build_static_actor_models(rom, by_model))
    from sm64_scratch.decoders.decode_locked_cap_boxes import build_locked_cap_box_models, build_locked_cap_box_payload
    restored.extend(build_locked_cap_box_models(rom))
    from sm64_scratch.decoders.decode_bob_expansion import (build_fast_coin_models, build_black_bobomb_model,
                                    build_bob_expansion_payload, build_cannon_opening_model)
    restored.append(build_cannon_opening_model(restored))
    restored.extend(build_fast_coin_models(rom))
    restored.append(build_black_bobomb_model(rom))
    restored.append(build_butterfly_model(rom))
    restored.append(build_bird_model(rom))
    toad, toad_rig = build_toad_model(rom)
    restored.append(toad)
    restored.append(build_flag_model(rom))
    restored.append(build_fish_model(rom, by_model['Castle Fish']))
    restored.append(build_painting_model(rom, by_model['Bob Painting']))
    restored.append(build_shadow_model())
    restored.append(build_scene_model(rom, metadata("lobby"), by_model.get('Castle Lobby', [])))
    mario = build_mario_model(rom)
    restored.append(mario[:3])
    head = build_head_model(rom)
    restored.append(head[:3])
    print('[3/5] Building collision grids and animation matrices...', flush=True)
    payload, collision_reports = {}, {}

    def merge(values, allow_override=()):
        overlap = set(payload) & set(values)
        if overlap - set(allow_override):
            raise ValueError('Duplicate runtime lists: ' + ', '.join(sorted(overlap)))
        payload.update(values)

    for name, recipe in metadata('collisions')['levels'].items():
        values, report = build_collision_payload(
            rom, base, segment_rom_offset=recipe['segment_rom_offset'],
            collision_pointer=int(recipe['collision_pointer'], 16),
            list_prefix=recipe['list_prefix'], grid=recipe['grid'],
            world_translation=recipe.get('world_translation', (0, 0, 0)),
            authored_walls=recipe.get('authored_walls', ()))
        merge(values)
        collision_reports[name] = report
    flight, flight_report = build_arrival_flight_payload(rom)
    flight['Intro.Flight.MeshStride'] = [lakitu[2]['vertices_per_frame'], lakitu[2]['faces_per_frame']]
    merge(flight)
    garden, garden_report = build_garden_runtime(rom)
    merge(garden)
    merge(build_garden_actor_runtime(rom))
    merge(build_door_animation_payload(rom))
    merge(build_coin_placement_payload(rom))
    merge(build_bob_objective_payload(rom))
    expanded_bob, expanded_bob_report = build_bob_expansion_payload(rom)
    merge(expanded_bob)
    merge(build_locked_cap_box_payload(rom))
    merge(build_goomba_runtime(rom))
    merge(build_chomp_animation_payload(rom))
    merge(build_buddy_animation_payload(rom))
    merge(build_battlefield_tree_payload(rom))
    merge(toad_rig)
    merge(build_toad_animation_payload(rom))
    merge(build_mario_gameplay_payload(rom))
    merge(mario[3])
    catalog = metadata("action_catalog")["entries"]
    merge({"Action.Name": [r["action"] for r in catalog],
           "Action.Group": [r["group"] for r in catalog],
           "Action.Clip": [r["animation"] for r in catalog]})
    matrices, scalars, matrix_report = build_mario_animation_payload(rom, base, mario)
    merge(matrices)
    sky, sky_report = build_sky_texture_payload(rom, metadata('sky'))
    merge(sky)
    intro = metadata('intro')
    scale, scale_report = build_logo_scale_payload(rom, intro)
    rotation, rotation_report = build_head_animation_payload(rom, intro)
    face, face_report = build_title_runtime_payload(rom)
    stars, star_report = build_star_animation_payload(rom)
    background, background_report = build_title_background(rom, metadata('background'))
    for values in (scale, face):
        merge(values)
    merge(build_title_background_runtime_payload(background),
          allow_override=("TitleIntro.Background240",))
    merge(stars, allow_override=tuple("TitleIntro." + color + field
                                     for color in ("RedStar", "SilverStar")
                                     for field in ("X", "Y", "Size", "Visible")))
    merge(rotation, allow_override=("TitleIntro.RootX", "TitleIntro.RootY", "TitleIntro.RootZ",
                                     "TitleIntro.RootRX", "TitleIntro.RootRY", "TitleIntro.RootRZ"))
    effects, effect_report = build_title_effect_assets(rom)
    reveal, reveal_report = build_title_star_reveal_assets(rom)
    effects.update(reveal)
    hud, hud_report = build_hud_assets(rom)
    transitions, transition_report = build_game_transition_assets(rom)
    if args.diagnostic_silent_audio:
        print('[4/5] Packaging a SILENT DIAGNOSTIC SB3; audio is not being verified...', flush=True)
        audio = _diagnostic_audio_assets()
    else:
        print('[4/5] Rendering ROM audio and packaging a local development SB3...', flush=True)
        audio = build_audio_assets(rom)
        audio.update(build_game_audio_assets(rom))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='game_phase_2_', dir=args.out.parent) as tmp:
        output = Path(tmp) / 'build.sb3'
        with zipfile.ZipFile(args.base) as source, zipfile.ZipFile(output, 'w') as target:
            for item in source.infolist():
                _write_project(target, item, json.dumps(base, separators=(',', ':')).encode()
                               if item.filename == 'project.json' else source.read(item.filename))
        add_game_models(output, by_model, restored)

        # Predecode the static BOB model sources after they have been added to
        # Pre-loaded, but before runtime lists are populated. This removes the
        # first-entry OBJ tokenizer cost while still deriving every byte from
        # the user's own ROM during this build.
        from sm64_scratch.patches.patch_bob_predecode_cache import apply_bob_predecode_cache
        with zipfile.ZipFile(output, 'r') as source:
            predecode_project = json.loads(source.read('project.json'))
            predecode_report = apply_bob_predecode_cache(predecode_project)
            patched_json = json.dumps(predecode_project, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            patched_tmp = output.with_name(output.name + '.predecode.tmp')
            with zipfile.ZipFile(patched_tmp, 'w') as target:
                for item in source.infolist():
                    _write_project(target, item, patched_json if item.filename == 'project.json' else source.read(item.filename))
        patched_tmp.replace(output)

        add_runtime_lists(output, payload, scalars)
        replace_costume_assets(output, costumes)
        replace_costume_assets(output, hud)
        replace_costume_assets(output, transitions)
        replace_costume_assets(output, build_cannon_reticle_assets(rom))
        replace_costume_assets(output, build_bob_overlay_assets())
        remove_base_notice(output)
        fixes = apply_intro_interaction_lighting_fixes(output)
        replace_title_background(output, background)
        replace_title_effect_assets(output, effects)
        audio_report = add_audio_assets(output, audio)
        feature_report = apply_game_features(output, rom, args.credit)
        # Final feature assets can replace earlier placeholders. Retain only
        # media still referenced by the completed project.
        compact_archive(output)
        print('[5/5] Checking inserted data and recording incomplete coverage...', flush=True)
        with zipfile.ZipFile(output) as archive:
            if archive.testzip():
                raise ValueError('Generated SB3 archive is corrupt')
            project = json.loads(archive.read('project.json'))
            for target in project['targets']:
                for media in target.get('costumes', []) + target.get('sounds', []):
                    if media['md5ext'] not in archive.namelist() or hashlib.md5(archive.read(media['md5ext'])).hexdigest() != media['assetId']:
                        raise ValueError('Missing or mismatched Scratch media: ' + media['name'])
        inserted = {v[0]: v[1] for t in project['targets'] for v in t.get('lists', {}).values()}
        for name, values in payload.items():
            if inserted.get(name) != values:
                raise ValueError('Inserted runtime list differs: ' + name)
        expected = json.loads((METADATA_DIR / 'reference_inventory.json').read_text())
        geometry = sorted(name for name, _, _ in restored)
        records = inserted['Pre-loaded'][::2]
        present = {name[1:] for name in records}
        missing_geometry = sorted(set(expected['models']) - set(geometry))
        report = {
            'status': 'DIAGNOSTIC_SILENT_AUDIO' if args.diagnostic_silent_audio else 'PLAYABLE_DEMO_INCOMPLETE',
            'diagnostic_silent_audio': args.diagnostic_silent_audio,
            'patch_pipeline': 'shared build_game.main for normal and diagnostic builds',
            'phase': 2, 'rom_sha1': ROM_SHA1,
            'gameplay_source_merge': base.get('extensionData', {}).get('gameplay_source_merge'),
            'base_sha256': hashlib.sha256(args.base.read_bytes()).hexdigest(),
            'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'packaging': 'Final referenced media only, ZIP DEFLATE level 6; compressed once after feature patches',
            'reference_model_count': len(expected['models']),
            'geometry_model_count': len(geometry), 'geometry_models': geometry,
            'texture_only_models': sorted(present - set(geometry)),
            'models_without_geometry': missing_geometry,
            'models_without_any_record': sorted(set(expected['models']) - present),
            'verified_assets': asset_report, 'collision_levels': collision_reports,
            'garden_runtime': garden_report,
            'pipe_camera_fixes': pipe_camera_fixes,
            'arrival_fixes': arrival_fixes,
            'pipe_forward_idle': base.get('extensionData', {}).get('pipe_forward_idle_fix', {}),
            'arrival_flight': {'logic': flight_fixes, 'runtime': flight_report},
            'presentation_fixes': presentation_fixes,
            'frame_pipeline': frame_pipeline,
            'keyboard_running': keyboard_running,
            'scene_lookup_fixes': scene_fixes,
            'bob_entry_stall_fix': bob_entry_stall,
            'fast_scene_create': fast_scene_create,
            'bob_predecode_cache': predecode_report,
            'castle_camera_grip_v2': castle_camera_grip_v2,
            'bob_sign_fixes': bob_sign_fixes,
            'dive_rollout_animation': dive_rollout_animation,
            'ledge_grab': ledge_grab,
            'triple_jump_chain_v30': triple_jump_chain,
            'red_coin_shadows_v30': red_coin_shadows,
            'native_star_exit': native_star_exit,
            'castle_door_decomp_rewrite': base.get('extensionData', {}).get('castle_door_decomp_rewrite', {}),
            'empty_required_static_lists': [row for row in expected['static_list_requirements']
                                           if not inserted.get(row['name'])],
            'runtime_lists_inserted': len(payload), 'mario_animations': matrix_report,
            'models': {name: data for name, _, data in restored},
            'gameplay_v13': gameplay_fixes, 'castle_door_v15': base['extensionData']['castle_door_v15'],
            'castle_transition_cache': base['extensionData'].get('castle_transition_cache', {}),
            'damage_camera_hud_v14': damage_camera_hud, 'beta010_v16': beta010,
            'course_hud_waterfall_v29': course_hud_waterfall,
            'logo_startup_v17': base['extensionData']['logo_startup_v17'],
            'session_death_camera_v19': base['extensionData']['session_death_camera_v19'],
            'compact_hud_v20': base['extensionData']['compact_hud_v20'],
            'bob_objectives_v21': bob_objectives,
            'red_coin_feedback_v22': red_coin_feedback,
            'runtime_stability_v23': runtime_stability,
            'live_transition_render_v24': base['extensionData']['live_transition_render_v24'],
            'bob_expansion_v23': bob_expansion,
            'cannon_view_v25': cannon_view,
            'locked_cap_boxes_v27': locked_cap_boxes,
            'bob_expansion_rom': expanded_bob_report,
            'hud': hud_report, 'gameplay_transitions': transition_report, 'sky': sky_report, 'audio': audio_report,
            'game_features': feature_report,
            'intro': {'scale': scale_report, 'rotation': rotation_report,
                      'face': face_report, 'stars': star_report, 'effects': effect_report,
                      'reveal': reveal_report, 'background': background_report, 'fixes': fixes},
            'limitations': ['Full-game reconstruction remains incomplete.',
                            'A record containing only textures does not restore a model.',
                            'Preserved gameplay logic does not imply restored runtime data.',
                            'ROM-decoded meshes still require visual comparison with the private reference.'],
        }
        output.replace(args.out)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(f'Local development project: {args.out}')
    print(f'Geometry: {len(geometry)}/{len(expected["models"])} models. Status: {report["status"]}')
    print(f'Report: {args.report}')


if __name__ == '__main__':
    main()
