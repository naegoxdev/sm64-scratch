# Architecture and source guide

## Build pipeline

1. The root `build_game.py` launcher loads `sm64_scratch.build_game` from `src/`.
2. The builder reads and validates the local ROM and `templates/BASE.sb3`.
3. It applies the existing Scratch block patches in their established order.
4. Decoders use `config/metadata/` recipes to reconstruct textures, models, animations, collisions, HUD elements, and audio.
5. Packaging helpers insert resources and runtime lists into the Scratch archive. The BOB cache patch predecodes model data at build time.
6. The builder compacts the archive and writes the playable `.sb3` and JSON report to `output/`.

Python is used to build the project. The generated Scratch game does not invoke Python while playing.

## Source map

| Location | Responsibility |
| --- | --- |
| `src/sm64_scratch/build_game.py` | Command-line arguments, reconstruction stages, patch ordering, build report. |
| `src/sm64_scratch/rom_codec.py` | ROM normalization, compression, and image decoding primitives. |
| `src/sm64_scratch/sb3_builder.py` | Scratch ZIP/project manipulation, intro resources, and audio insertion. |
| `src/sm64_scratch/game_builder.py` | Gameplay model/media insertion and archive compaction. |
| `src/sm64_scratch/decoders/decode_*.py` | Resource-specific reconstruction from ROM bytes. |
| `src/sm64_scratch/patches/patch_*.py` | Gameplay/presentation changes expressed as Scratch block graph edits. |
| `src/sm64_scratch/patches/patch_animation.py` | Shared `Graph` helper used to construct Scratch blocks, plus animation patches. |
| `src/sm64_scratch/paths.py` | Repository, metadata, template, and output paths. |
| `src/sm64_scratch/maintenance/sync_logic.py` | Logic-only synchronization from a local development build. |
| `config/metadata/` | ROM extraction recipes, conversion parameters, aliases, and reference inventory. |
| `templates/BASE.sb3` | Distributable assetless Scratch project. |

Internal imports use the `sm64_scratch` package. Subpackage initializers do not eagerly import modules; some decoder/patch dependencies are intentionally imported inside functions.

## Changing gameplay

Start with the relevant patch under `patches/` and its call in `build_game.py`. Many patches depend on procedures or lists created by earlier stages. Preserve patch order unless the change specifically requires altering it.

Changes to a decoder or recipe affect reconstructed resources. Changes to a patch affect generated Scratch logic. Rebuild the project to see either kind of change; moving files in this repository does not itself change the game logic stored in the template.

The source reorganization preserves existing patch names, gameplay implementations, and patch order. Default resource paths are centralized instead of depending on each decoder's physical location.

## Developer tools

These commands use the same environment as a regular build. Run them from the repository root.

### Synchronize the assetless template

```bash
python tools/sync_logic.py --full "/path/to/private-development.sb3"
```

The existing synchronization tool writes `local_build/BASE_UPDATED.sb3` and `local_build/sync_report.json`. It copies logic while stripping private media and list values. Review the generated template and report before replacing `templates/BASE.sb3`; new resources may still need decoders or recipes. Do not use a ROM-populated gameplay build as the public template.

### Silent diagnostic build

```bash
python tools/build_game_verify.py --rom "/path/to/US-ROM.z64"
```

This existing diagnostic mode follows the normal patch pipeline but replaces audio with short silent clips. Its output is `output/MARIO64_SCRATCH_DIAGNOSTIC_SILENT.sb3` and `output/diagnostic_report.json`; it cannot establish audio fidelity.

## Local files

The existing `.gitignore` excludes generated projects, build folders, virtual environments, Python caches, and ROM files. `templates/BASE.sb3` is the tracked template exception. Keep extracted resource dumps and local reference material outside the public source tree.
