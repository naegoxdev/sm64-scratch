#!/usr/bin/env python3
"""Read the scene display lists and their images from the user's ROM."""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from PIL import Image

from sm64_scratch.decoders.decode_models import build_scene_model_text, read_intro_logo
from sm64_scratch.rom_codec import decode_ia16, decode_rgba16, mio0, ROM_SHA1
import math


from sm64_scratch.paths import METADATA_DIR
DEFAULT_RECIPE = METADATA_DIR / "lobby.json"


def build_scene_model(rom: bytes, recipe: dict | None = None, verified_textures=()):
    recipe = recipe or json.loads(DEFAULT_RECIPE.read_text(encoding="utf-8"))
    digest = hashlib.sha1(rom).hexdigest()
    if recipe.get("format") != 13 or recipe.get("rom_sha1") != digest or digest != ROM_SHA1:
        raise ValueError("scene recipe does not match the supported US ROM")

    segments = {
        int(segment_id): mio0(rom, row["rom_offset"])
        for segment_id, row in recipe["segments"].items()
    }
    if recipe.get('material_bindings'):
        return _build_calibrated_scene(rom, recipe, segments, verified_textures)
    roots = [int(pointer, 16) for pointer in recipe["display_list_pointers"]]
    texture_sizes: dict[tuple[int, int], set[tuple[int, int]]] = {}
    command_count = 0
    for root in roots:
        root_segment = root >> 24
        triangles, commands = read_intro_logo(segments[root_segment], root, None)
        command_count += commands
        for triangle in triangles:
            pointer = triangle["texture_pointer"]
            if pointer is not None:
                texture_sizes.setdefault((root, pointer), set()).add(
                    tuple(triangle["texture_size"]))

    # A texture used with two different tile extents needs separate Scratch names,
    # because the model format stores material bindings per display-list root.
    texture_specs = []
    model_textures = []
    texture_hashes = {}
    formats = recipe.get("texture_formats", {})
    for (root, pointer), size_set in sorted(texture_sizes.items()):
        if len(size_set) != 1:
            raise ValueError(f"ROM uses incompatible tile extents for {pointer:#010x}: {size_set}")
        width, height = next(iter(size_set))
        segment_id = pointer >> 24
        if segment_id not in segments:
            raise ValueError(f"Texture addresses an undeclared segment: {pointer:#010x}")
        offset = pointer & 0xFFFFFF
        fmt = formats.get(f"0x{pointer:08X}", recipe["segments"][str(segment_id)]["texture_format"])
        source = segments[segment_id]
        required = width * height * (1 if fmt == "ia8" else 2)
        if offset < 0 or offset + required > len(source):
            raise ValueError(f"Texture is outside the ROM segment: {pointer:#010x} ({width}x{height})")
        if fmt == "rgba16":
            image = decode_rgba16(source, offset, width, height, "floor")
        elif fmt == "ia16":
            image = decode_ia16(source, offset, width, height)
        elif fmt == "ia8":
            from sm64_scratch.rom_codec import decode_ia8
            image = decode_ia8(source, offset, width, height)
        else:
            raise ValueError(f"Unsupported ROM texture format: {fmt}")

        # Scratch's existing newtex material records are RGB. Keep the ROM's
        # original colour channels; alpha-bearing effects remain a known limit.
        rgba = image.convert("RGBA")
        image = Image.new("RGB", rgba.size)
        image.putdata([(r, g, b) if a else (254, 1, 254) for r, g, b, a in rgba.getdata()])
        texture_name = f"SceneROM_{pointer:08X}_{root & 0xFFFFFF:06X}"
        material = f"SceneMat_{pointer:08X}_{root & 0xFFFFFF:06X}"
        texture_specs.append({
            "pointer": f"0x{pointer:08X}",
            "root_pointer": f"0x{root:08X}",
            "texture": texture_name,
            "material": material,
        })
        model_textures.append((texture_name, image))
        texture_hashes[texture_name] = hashlib.sha256(image.tobytes()).hexdigest()

    filtered_recipe = dict(recipe)
    filtered_recipe["textures"] = texture_specs
    filtered_recipe["display_list_pointers"] = [f"0x{root:08X}" for root in roots]
    filtered_recipe["object"] = recipe.get("object", "Arena")
    filtered_recipe["coordinate_divisor"] = recipe.get("coordinate_divisor", 100)
    filtered_recipe["vertex_color_atlas"] = {
        "texture": "SceneROM_VertexColors",
        "material": "SceneROM_VertexColorsMat",
        "tile_size": 16,
    }

    fingerprint = []
    for root in roots:
        triangles, _ = read_intro_logo(segments[root >> 24], root, None)
        shift = recipe.get("root_axis_shifts", {}).get(f"0x{root:08X}", [0, 0, 0])
        fingerprint.extend(tuple(sorted(tuple(round(v["position"][axis] + shift[axis])
                        for axis in range(3)) for v in triangle["vertices"])) for triangle in triangles)
    geometry_sha256 = hashlib.sha256(json.dumps(sorted(fingerprint), separators=(",", ":")).encode()).hexdigest()
    if geometry_sha256 != recipe.get("expected_geometry_sha256"):
        raise ValueError("Scene triangle geometry does not match the private reference fingerprint")
    model_text, report = build_scene_model_text(
        segments[7], filtered_recipe, model_textures)
    report.update({
        "model": recipe["model"],
        "geometry_sha256": geometry_sha256,
        "geometry_matches_private_reference": True,
        "rom_sha1": digest,
        "display_list_roots": len(roots),
        "display_list_commands": command_count,
        "texture_images_read_from_rom": len(model_textures),
        "texture_hashes_rgb": texture_hashes,
        "known_limitations": [
            "Material alpha is discarded when serializing to this project\'s RGB newtex format.",
            "ROM vertex colors are not yet baked into every textured face."
        ],
    })
    return recipe["model"], model_text, report


def _build_calibrated_scene(rom, recipe, segments, verified_textures):
    images = dict(verified_textures)
    for name, spec in recipe.get('solid_materials', {}).items():
        images[name] = Image.new('RGB', spec['size'], tuple(spec['rgb']))
    specs, rows, commands = {}, [], 0
    for root in recipe['display_list_pointers']:
        triangles, count = read_intro_logo(segments[int(root, 16) >> 24], int(root, 16), None)
        commands += count
        shift = recipe.get('root_axis_shifts', {}).get(root, [0, 0, 0])
        for triangle in triangles:
            pointer = triangle['texture_pointer']
            candidates = [b for b in recipe['material_bindings']
                          if b['root_pointer'] == root
                          and (int(b['pointer'], 16) if b['pointer'] else None) == pointer
                          and all(any(lo <= v['source_address'] <= hi
                                      for lo, hi in b['address_ranges'])
                                  for v in triangle['vertices'])]
            if len(candidates) != 1:
                raise ValueError(f'Ambiguous scene material at {root}: {pointer}')
            binding = candidates[0]
            name = binding['texture']
            if name not in images:
                raise ValueError('Missing calibrated scene texture: ' + name)
            specs[binding['material']] = {**binding, 'selection_key': binding['material']}
            vertices = []
            width, height = binding['image_size']
            for vertex in triangle['vertices']:
                if binding['uv_method'] == 'constant':
                    uv = binding['uv_constant']
                else:
                    uv = []
                    for st, size in zip(vertex['st'], (width, height)):
                        value = st / (32 * size)
                        tile = math.floor(value)
                        uv.append(tile + (value - tile) * (1 - 1 / size) + .5 / size)
                vertices.append({**vertex, 'uv': uv, 'position': tuple(
                    vertex['position'][a] + shift[a] for a in range(3))})
            rows.append((int(root, 16), {**triangle, 'vertices': vertices,
                                      'material': binding['material']}))
    filtered = {**recipe, 'textures': list(specs.values()), 'vertex_color_atlas': None}
    model, report = build_scene_model_text(segments[7], filtered, list(images.items()), rows)
    report.update(model=recipe['model'], display_list_commands=commands,
                  material_bindings=len(specs), verified_texture_count=len(images),
                  uv_max_reference_error=max(b['uv_max_error'] for b in recipe['material_bindings']),
                  known_limitations=['Material and UV numerical comparisons pass; native visual verification remains pending.'])
    return recipe['model'], model, report
