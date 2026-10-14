# Building the game

Run commands from the repository root unless stated otherwise. Python performs the reconstruction at build time; the generated game runs as Scratch blocks in TurboWarp.

## Requirements

- Python 3.10 or newer with `pip` and `venv`.
- The dependencies in `requirements.txt` (Pillow and NumPy).
- Your own compatible Super Mario 64 USA ROM, in `.z64`, `.n64`, or `.v64` byte order.
- TurboWarp to play the generated project.

The builder normalizes the ROM byte order and requires SHA-1 `9bef1128717f958171a4afac3ed78ee2bb4e86ce`.

## Build helpers

### Windows

Double-click `BUILD_WINDOWS.bat`, then supply the ROM path. Install Python with the `py` launcher or make `python` available on PATH.

### macOS / Linux

```bash
bash BUILD_MAC_LINUX.sh "/path/to/your/US-ROM.z64"
```

Without an argument, the helper prompts for the ROM path. Both helpers create `.venv/`, install dependencies, and invoke the same public `build_game.py` entry point.

## Manual build

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe build_game.py --rom "C:\path\to\your\US-ROM.z64"
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python build_game.py --rom "/path/to/your/US-ROM.z64"
```

No package installation is required for this source checkout: the public launcher loads the modules under `src/`.

## Output and options

By default the builder writes `output/MARIO64_SCRATCH.sb3` and `output/build_report.json` under the repository root. Open the `.sb3` in TurboWarp, click the green flag, and press Space or Enter on the Mario head screen.

```bash
python build_game.py --rom "/path/to/US-ROM.z64" --out "output/custom.sb3" --report "output/custom_report.json"
```

Other options:

| Option | Purpose |
| --- | --- |
| `--base PATH` | Use an alternative assetless template; default is `templates/BASE.sb3`. |
| `--credit TEXT` | Set the author credit shown during the logo. |
| `--diagnostic-silent-audio` | Produce a diagnostic build with silence replacing audio. This is not the normal playable release. |
| `--help` | Show all command-line options. |

Explicit paths are resolved relative to your current directory. The default template, recipe, and output paths are anchored to the repository, so moving Python files into `src/` does not change their meaning.

## Troubleshooting

- **Unsupported ROM:** check the region, revision, and normalized SHA-1 above. Renaming a file does not change its contents.
- **Missing Pillow or NumPy:** install `requirements.txt` with the same Python interpreter used to run the build.
- **Missing template or recipes:** keep `src/`, `config/`, and `templates/` together in the repository checkout.
- **`PLAYABLE_DEMO_INCOMPLETE`:** expected for this beta. Inspect the report for details about restored resources and remaining limitations.
- **No keyboard input:** click inside the TurboWarp stage before using the controls.

See [Playing and limitations](../README.md#playing-and-limitations) for gameplay input.
