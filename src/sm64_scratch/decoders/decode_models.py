#!/usr/bin/env python3
"""Rebuild selected SM64 model records directly from the user's US ROM.

This module deliberately contains offsets and decoding logic only. Geometry,
vertex colours, and texture pixels are read from the ROM passed by the user and
are written only to a caller-selected local output directory.
"""
from __future__ import annotations
from sm64_scratch.paths import METADATA_DIR

import argparse
import hashlib
import json
import math
import re
import struct
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

from sm64_scratch.rom_codec import (
    ROM_SHA1,
    decode_rgba16,
    mio0,
    normalized_rom,
    source_sha,
)


DEFAULT_MANIFEST = METADATA_DIR / "models.json"


class ModelDecodeError(ValueError):
    pass


def _vtx(segment: bytes, pointer: int, count: int, segment_id: int = 7):
    if pointer >> 24 != segment_id:
        raise ModelDecodeError(
            f"Vertex pointer outside segment {segment_id}: {pointer:#010x}")
    offset = pointer & 0xFFFFFF
    if count < 1 or offset + 16 * count > len(segment):
        raise ModelDecodeError(f"Vertices outside segment: {pointer:#010x} ({count})")
    result = []
    for index in range(count):
        start = offset + 16 * index
        x, y, z, _flag, s, t = struct.unpack_from(">hhhHhh", segment, start)
        result.append({
            "position": (x, -z, y),
            "st": (s, t),
            "color": tuple(segment[start + 12:start + 16]),
            "source_address": offset + 16 * index,
        })
    return result


def read_intro_logo(segment: bytes, display_list_pointer: int,
                    texture_pointers: set[int] | None, segment_id: int = 7):
    """Interpret the F3D-old display-list commands used by a ROM model."""
    cache = [None] * 16
    state = {"texture": None, "textured": False, "tile_size": (32, 32),
             "light_group": None, "light_pointer": None, "cull_back": True}
    triangles = []
    command_count = 0
    active_lists = set()

    def run(pointer: int, depth: int = 0):
        nonlocal command_count
        if depth > 32:
            raise ModelDecodeError("Display-list depth exceeds 32")
        if pointer >> 24 != segment_id:
            raise ModelDecodeError(
                f"Display list outside segment {segment_id}: {pointer:#010x}")
        offset = pointer & 0xFFFFFF
        if offset in active_lists:
            raise ModelDecodeError(f"Recursive display-list call: {pointer:#010x}")
        start_offset = offset
        active_lists.add(start_offset)
        try:
            while offset + 8 <= len(segment):
                command_count += 1
                if command_count > 500_000:
                    raise ModelDecodeError("Display-list command limit exceeded")
                w0, w1 = struct.unpack_from(">II", segment, offset)
                opcode = w0 >> 24
                offset += 8
                if opcode == 0xB8:  # G_ENDDL
                    return
                if opcode == 0x06:  # G_DL
                    run(w1, depth + 1)
                    if (w0 >> 16) & 0xFF:  # G_DL_NOPUSH: tail call
                        return
                    continue
                if opcode == 0x04:  # G_VTX (F3D old: n-1 and v0)
                    packed = (w0 >> 16) & 0xFF
                    count = (packed >> 4) + 1
                    first = packed & 0x0F
                    loaded = _vtx(segment, w1, count, segment_id)
                    for index, vertex in enumerate(loaded):
                        slot = first + index
                        if slot >= len(cache):
                            raise ModelDecodeError(f"Invalid G_VTX cache index: {slot}")
                        cache[slot] = vertex
                    continue
                if opcode == 0xFD:  # G_SETTIMG
                    state["texture"] = w1
                    continue
                if opcode == 0xF2:  # G_SETTILESIZE (10.2 fixed-point bounds)
                    uls, ult = (w0 >> 12) & 0xFFF, w0 & 0xFFF
                    lrs, lrt = (w1 >> 12) & 0xFFF, w1 & 0xFFF
                    width = max(1, ((lrs - uls) >> 2) + 1)
                    height = max(1, ((lrt - ult) >> 2) + 1)
                    state["tile_size"] = (width, height)
                    continue
                if opcode == 0x03:  # G_MOVEMEM: actor light pointer
                    if w1 >> 24 == segment_id:
                        state["light_pointer"] = w1
                    if w1 >> 24 == segment_id and segment_id == 4:
                        light_offset = w1 & 0xFFFFFF
                        if light_offset < 0x90:
                            state["light_group"] = min(5, light_offset // 0x18)
                    continue
                if opcode == 0xBB:  # G_TEXTURE
                    state["textured"] = bool(w0 & 1)
                    continue
                if opcode == 0xB6:  # G_CLEARGEOMETRYMODE (F3D old)
                    if w1 & 0x2000:
                        state["cull_back"] = False
                    continue
                if opcode == 0xB7:  # G_SETGEOMETRYMODE (F3D old)
                    if w1 & 0x2000:
                        state["cull_back"] = True
                    continue
                if opcode == 0xBF:  # G_TRI1
                    encoded = ((w1 >> 16) & 0xFF, (w1 >> 8) & 0xFF, w1 & 0xFF)
                    indices = tuple(value // 10 for value in encoded)
                    triangles.append(_make_triangle(cache, indices, state, texture_pointers))
                    continue
                if opcode == 0xB1:  # G_TRI2
                    first = tuple((w0 >> shift) & 0x7F for shift in (17, 9, 1))
                    second = tuple((w1 >> shift) & 0x7F for shift in (17, 9, 1))
                    triangles.append(_make_triangle(cache, first, state, texture_pointers))
                    triangles.append(_make_triangle(cache, second, state, texture_pointers))
                    continue
            raise ModelDecodeError(f"Display list without G_ENDDL: {pointer:#010x}")
        finally:
            active_lists.remove(start_offset)

    run(display_list_pointer)
    return triangles, command_count


def _make_triangle(cache, indices, state, texture_pointers):
    if len(set(indices)) != 3 or any(index < 0 or index >= 16 for index in indices):
        raise ModelDecodeError(f"Invalid GBI triangle indices: {indices}")
    vertices = [cache[index] for index in indices]
    if any(vertex is None for vertex in vertices):
        raise ModelDecodeError(f"GBI triangle references unloaded vertex cache: {indices}")
    texture_pointer = state["texture"] if state["textured"] else None
    if (texture_pointer is not None and texture_pointers is not None
            and texture_pointer not in texture_pointers):
        raise ModelDecodeError(f"GBI texture without ROM recipe: {texture_pointer:#010x}")
    return {"vertices": vertices, "texture_pointer": texture_pointer,
            "texture_size": state["tile_size"],
            "light_group": state["light_group"], "light_pointer": state["light_pointer"],
            "cull_back": state["cull_back"]}


def _rgb_texture(segment, source_offset):
    image = decode_rgba16(segment, source_offset, 32, 32, "floor")
    if image.getchannel("A").getextrema() != (255, 255):
        raise ModelDecodeError("Intro texture alpha cannot be represented by RGB newtex")
    return image.convert("RGB")


def _color_atlas(triangles, tile_size=16):
    """Bake ROM Vtx RGB values as triangle-local patches for Scratch newtex."""
    count = len(triangles)
    if not count:
        return Image.new("RGB", (1, 1), (0, 0, 0)), {}
    columns = math.ceil(math.sqrt(count))
    rows = math.ceil(count / columns)
    image = Image.new("RGB", (columns * tile_size, rows * tile_size), (0, 0, 0))
    uv_by_index = {}
    corner_min = 1
    corner_max = tile_size - 2
    span = corner_max - corner_min
    pixels = image.load()
    for atlas_index, triangle in enumerate(triangles):
        col, row = atlas_index % columns, atlas_index // columns
        origin_x, origin_y = col * tile_size, row * tile_size
        colors = [vertex["color"][:3] for vertex in triangle["vertices"]]
        uv_by_index[id(triangle)] = (
            ((origin_x + corner_min) / image.width, (origin_y + corner_min) / image.height),
            ((origin_x + corner_max) / image.width, (origin_y + corner_min) / image.height),
            ((origin_x + corner_min) / image.width, (origin_y + corner_max) / image.height),
        )
        for py in range(tile_size):
            for px in range(tile_size):
                a = max(0.0, min(1.0, (px - corner_min) / span))
                b = max(0.0, min(1.0, (py - corner_min) / span))
                if a + b > 1.0:
                    total = a + b
                    a, b = a / total, b / total
                c = 1.0 - a - b
                rgb = tuple(round(c * colors[0][channel]
                                  + a * colors[1][channel]
                                  + b * colors[2][channel])
                            for channel in range(3))
                pixels[origin_x + px, origin_y + py] = rgb
    return image, uv_by_index


def _modulate_texture(image, colour):
    """Bake the F3D texture*vertex-colour combiner used by the title logo."""
    cr, cg, cb = colour
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b = pixels[x, y]
            pixels[x, y] = (
                (r * cr + 127) // 255,
                (g * cg + 127) // 255,
                (b * cb + 127) // 255,
            )
    return image


def _logo_combined_atlas(triangles, source_images, tile_size=32):
    """Build a compact atlas for textured logo triangles.

    The N64 title display list modulates each source texture with the RGB
    value in its vertices.  Scratch's model material has no vertex-colour
    combiner, so the same operation is baked into one ROM-derived atlas.
    Most triangles use one constant colour and share a tile; the few
    interpolated triangles use the average colour, which avoids seams while
    preserving the original texture detail.
    """
    groups = {}
    triangle_keys = {}
    for triangle in triangles:
        pointer = triangle["texture_pointer"]
        colours = tuple(vertex["color"][:3] for vertex in triangle["vertices"])
        if len(set(colours)) == 1:
            key = (pointer, colours[0])
        else:
            mean = tuple(round(sum(c[channel] for c in colours) / 3)
                         for channel in range(3))
            key = (pointer, mean, id(triangle))
        if key not in groups:
            groups[key] = len(groups)
        triangle_keys[id(triangle)] = groups[key]

    columns = 8
    rows = max(1, math.ceil(len(groups) / columns))
    atlas = Image.new("RGB", (columns * tile_size, rows * tile_size), (0, 0, 0))
    for key, index in groups.items():
        pointer, colour, *_ = key
        tile = source_images[pointer].copy()
        tile = _modulate_texture(tile, colour)
        x = (index % columns) * tile_size
        y = (index // columns) * tile_size
        atlas.paste(tile, (x, y))

    uv_by_index = {}
    for triangle in triangles:
        index = triangle_keys[id(triangle)]
        x = (index % columns) * tile_size
        y = (index // columns) * tile_size
        uvs = []
        for vertex in triangle["vertices"]:
            # The original display list uses wrapping texture coordinates.
            # Fold each coordinate into this tile before normalising it to the
            # combined atlas, retaining the exact source texel address.
            u = (vertex["st"][0] / 1024.0) % 1.0
            v = (vertex["st"][1] / 1024.0) % 1.0
            uvs.append(((x + u * tile_size + 0.5) / atlas.width,
                        (y + v * tile_size + 0.5) / atlas.height))
        uv_by_index[id(triangle)] = tuple(uvs)
    return atlas, uv_by_index, len(groups)


def _fmt(value):
    if abs(value) < 0.0000005:
        value = 0.0
    return f"{value:.8f}"


def _texture_tokens(name, image):
    # The title-head light atlas is made of uniform 128-row bands.  Expanding
    # every one of its 2.7 million pixels through nested Python f-strings is
    # needlessly slow; Scratch still receives the same explicit `c` stream
    # when each band is repeated as a string chunk.
    if image.size == (32, 4096):
        chunks = ["newtex", name, "32", "4096"]
        for band in range(32):
            # Lighting is constant vertically within each 128-row band, but
            # every atlas column is a different material. Preserve all 32
            # columns while repeating the scanline for the band.
            y = min(4095, band * 128)
            row = " ".join(
                "c " + "".join(f"{channel:02X}" for channel in image.getpixel((x, y)))
                for x in range(32)
            )
            chunks.append((row + " ") * 128)
        return ' '.join(chunks).strip()
    tokens = ["newtex", name, str(image.width), str(image.height)]
    for color in image.getdata():
        tokens.extend(("c", "".join(f"{channel:02X}" for channel in color)))
    return " ".join(tokens)


def build_model_text(segment, recipe):
    texture_specs = {int(row["pointer"], 16): row for row in recipe["textures"]}
    color_spec = recipe["vertex_color_atlas"]
    triangles, command_count = read_intro_logo(
        segment, int(recipe["display_list_pointer"], 16), set(texture_specs))
    if len(triangles) < recipe.get("minimum_triangles", 1):
        raise ModelDecodeError(
            f"Mesh {recipe['model']}incomplete: only {len(triangles)}triangles")

    color_triangles = [triangle for triangle in triangles if triangle["texture_pointer"] is None]
    textured_triangles = [triangle for triangle in triangles
                          if triangle["texture_pointer"] is not None]
    color_atlas, color_uv = _color_atlas(
        color_triangles, color_spec.get("tile_size", 16))
    source_images = {
        pointer: _rgb_texture(segment, int(spec["decoded_offset"]))
        for pointer, spec in texture_specs.items()
    }
    combined_atlas, combined_uv, combined_tiles = _logo_combined_atlas(
        textured_triangles, source_images)

    tokens = ["o", recipe["object"]]
    vertex_index = 0
    uv_index = 0
    faces = []
    texture_material = {
        pointer: ("TitleLogo_COMBINED", "TitleLogo_MCOMB")
        for pointer in texture_specs
    }
    texture_material[None] = (color_spec["texture"], color_spec["material"])
    position_lines = []
    uv_lines = []
    for triangle in triangles:
        material = texture_material[triangle["texture_pointer"]][1]
        refs = []
        if triangle["texture_pointer"] is None:
            uvs = color_uv[id(triangle)]
        else:
            uvs = combined_uv[id(triangle)]
        for vertex, uv in zip(triangle["vertices"], uvs):
            vertex_index += 1
            uv_index += 1
            x, y, z = vertex["position"]
            position_lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
            uv_lines.append(f"vt {_fmt(uv[0])} {_fmt(uv[1])}")
            refs.append(f"{vertex_index}/{uv_index}/1")
        faces.append((material, refs))

    material_triangles = Counter(material for material, _refs in faces)

    tokens.extend(position_lines)
    tokens.extend(uv_lines)
    tokens.append("vn 0.000000 1.000000 0.000000")
    tokens.append(_texture_tokens("TitleLogo_COMBINED", combined_atlas))
    tokens.append(_texture_tokens(color_spec["texture"], color_atlas))
    tokens.append(_material_tokens("TitleLogo_MCOMB", "TitleLogo_COMBINED"))
    tokens.append(_material_tokens(color_spec["material"], color_spec["texture"]))
    current_material = None
    for material, refs in faces:
        if material != current_material:
            tokens.extend(("usemtl", material))
            current_material = material
        tokens.append("f " + " ".join(refs))
        tokens.append("f " + " ".join((refs[0], refs[2], refs[1])))
    return " ".join(tokens), {
        "display_list_commands": command_count,
        "rom_triangles": len(triangles),
        "model_faces_including_two_sided": 2 * len(triangles),
        "vertex_color_triangles": len(color_triangles),
        "combined_texture_tiles": combined_tiles,
        "vertex_count": vertex_index,
        "texture_triangles": {
            spec["texture"]: sum(triangle["texture_pointer"] == pointer for triangle in triangles)
            for pointer, spec in texture_specs.items()
        },
        "material_faces_including_two_sided": {
            material: 2 * triangle_count
            for material, triangle_count in material_triangles.items()
        },
        "texture_images": {
            "TitleLogo_COMBINED": {
                "size": list(combined_atlas.size),
                "rgb_sha256": hashlib.sha256(combined_atlas.tobytes()).hexdigest(),
            },
        } | {
            color_spec["texture"]: {
                "size": list(color_atlas.size),
                "rgb_sha256": hashlib.sha256(color_atlas.tobytes()).hexdigest(),
            }
        },
    }


def _material_tokens(material, texture):
    return (f"newmtl {material} Ka 1 1 1 Kd 1 1 1 Ks 0 0 0 "
            f"map_Kd {texture}")


def build_scene_model_text(segment: bytes, recipe: dict,
                           model_textures: list[tuple[str, Image.Image]],
                           decoded_root_rows=None):
    """Flatten several ROM display lists as one Scratch OBJ-style model record."""
    roots = [int(value, 16) for value in recipe["display_list_pointers"]]
    texture_specs = recipe.get("textures", [])
    pointer_set = {int(row["pointer"], 16) for row in texture_specs if row.get('pointer')}
    root_rows = []
    command_count = 0
    for root in ([] if decoded_root_rows is not None else roots):
        triangles, commands = read_intro_logo(segment, root, pointer_set)
        shift = recipe.get("root_axis_shifts", {}).get(f"0x{root:08X}", [0, 0, 0])
        if any(shift):
            triangles = [{**triangle, "vertices": [
                {**v, "position": tuple(v["position"][axis] + shift[axis] for axis in range(3))}
                for v in triangle["vertices"]]} for triangle in triangles]
        root_rows.extend((root, triangle) for triangle in triangles)
        command_count += commands
    if decoded_root_rows is not None:
        root_rows = decoded_root_rows
    minimum = recipe.get("minimum_triangles", 1)
    if len(root_rows) < minimum:
        raise ModelDecodeError(
            f"Mesh {recipe['model']}incomplete: only {len(root_rows)}triangles")

    color_triangles = [triangle for _root, triangle in root_rows
                       if triangle["texture_pointer"] is None and not triangle.get('material')]
    color_spec = recipe.get("vertex_color_atlas")
    color_atlas = None
    color_uv = {}
    if color_triangles:
        if not color_spec:
            raise ModelDecodeError(
                f"{recipe['model']}has colored triangles without an atlas recipe")
        color_atlas, color_uv = _color_atlas(
            color_triangles, color_spec.get("tile_size", 16))

    texture_images = dict(model_textures)
    if color_spec:
        texture_images[color_spec["texture"]] = color_atlas
    texture_names = {row["texture"] for row in texture_specs}
    if color_spec:
        texture_names.add(color_spec["texture"])
    missing_textures = texture_names - texture_images.keys()
    if missing_textures:
        raise ModelDecodeError("Missing verified ROM textures for " + recipe["model"]
                               + ": " + ", ".join(sorted(missing_textures)))

    vertex_map = {}
    vertices = []
    texcoords = []
    normals = []
    faces = []
    texture_triangles = Counter()
    material_texture = {}
    root_materials = {}
    for row in texture_specs:
        pointer = int(row["pointer"], 16) if row.get("pointer") is not None else None
        root = int(row["root_pointer"], 16) if row.get("root_pointer") else None
        key = (root, pointer)
        if key in root_materials and not row.get("selection_key"):
            raise ModelDecodeError("Duplicate texture recipe in " + recipe["model"])
        root_materials[key] = row
        material_texture[row["material"]] = row["texture"]
    selected_materials = {row['material']: row for row in texture_specs}
    if color_spec:
        material_texture[color_spec["material"]] = color_spec["texture"]

    current_material = None
    coordinate_scale = float(recipe.get("coordinate_divisor", 100))
    if coordinate_scale <= 0:
        raise ModelDecodeError("Invalid coordinate divisor")
    for root, triangle in root_rows:
        pointer = triangle["texture_pointer"]
        row = root_materials.get((root, pointer)) or root_materials.get((None, pointer))
        if triangle.get('material'):
            row = selected_materials[triangle['material']]
        if pointer is None and color_spec and not triangle.get('material'):
            row = color_spec
        if row is None:
            raise ModelDecodeError(
                "Texture without ROM material for " + recipe["model"] + ": "
                + ("no texture" if pointer is None else f"{pointer:#010x}"))
        material = row["material"]
        if recipe.get("rom_geometry_culling") and not triangle["cull_back"]:
            # A texture can occur on both one-sided walls and two-sided fences.
            # Keep separate materials, sharing the same image and face count.
            material += "_TwoSided"
            selected_materials[material] = {**row, "nocull": True}
            material_texture[material] = row["texture"]
        texture_triangles[row["texture"]] += 1
        ordered = [triangle["vertices"][index] for index in (0, 2, 1)]
        refs = []
        positions = []
        for corner, vertex in zip((0, 2, 1), ordered):
            position = tuple(component / coordinate_scale
                             for component in vertex["position"])
            if 'uv' in vertex:
                uv = tuple(vertex['uv'])
            elif pointer is None and not triangle.get('material'):
                uv = color_uv[id(triangle)][corner]
            else:
                if recipe.get("normalize_uv_by_image_size"):
                    image = texture_images[row["texture"]]
                    uv = (vertex["st"][0] / (32.0 * image.width),
                          vertex["st"][1] / (32.0 * image.height))
                    if recipe.get("uv_texel_center_inset"):
                        # Match the authored exporter: retain integer repeats
                        # while mapping each tile to its first/last texel center.
                        uv = tuple(math.floor(value) +
                                   (value - math.floor(value)) * (1 - 1 / size) +
                                   .5 / size for value, size in zip(uv, image.size))
                else:
                    uv = tuple(component / 1024.0 for component in vertex["st"])
            key = (position, uv)
            if key not in vertex_map:
                vertex_index = len(vertices) + 1
                vertex_map[key] = vertex_index
                vertices.append(position)
                texcoords.append(uv)
            vertex_index = vertex_map[key]
            refs.append(vertex_index)
            positions.append(position)

        first, second, third = positions
        edge1 = tuple(second[i] - first[i] for i in range(3))
        edge2 = tuple(third[i] - first[i] for i in range(3))
        normal = (
            edge1[1] * edge2[2] - edge1[2] * edge2[1],
            edge1[2] * edge2[0] - edge1[0] * edge2[2],
            edge1[0] * edge2[1] - edge1[1] * edge2[0],
        )
        magnitude = math.sqrt(sum(component * component for component in normal))
        if magnitude == 0:
            raise ModelDecodeError("ROM mesh has a degenerate face")
        # This engine imports clockwise OBJ faces. Their stored outward normal
        # is the negative cross product, as in the authored reference meshes.
        normal = tuple(round(-component / magnitude, 6) for component in normal)
        normals.append(normal)
        face_refs = [f"{index}/{index}/{len(normals)}" for index in refs]
        faces.append((material, face_refs))

    tokens = ["o", recipe["object"]]
    tokens.extend("v " + " ".join(_fmt(value) for value in point) for point in vertices)
    tokens.extend("vt " + " ".join(_fmt(value) for value in uv) for uv in texcoords)
    tokens.extend("vn " + " ".join(_fmt(value) for value in normal) for normal in normals)
    for name, image in sorted(texture_images.items()):
        if name in texture_names:
            tokens.extend(_texture_tokens(name, image).split())
    for material, texture in sorted(material_texture.items()):
        text = _material_tokens(material, texture)
        if selected_materials.get(material, {}).get('nocull'):
            text = text.replace(' map_Kd ', ' nocull map_Kd ')
        tokens.extend(text.split())
    for material, refs in faces:
        if material != current_material:
            tokens.extend(("usemtl", material))
            current_material = material
        tokens.append("f " + " ".join(refs))

    return " ".join(tokens), {
        "display_list_commands": command_count,
        "rom_triangles": len(root_rows),
        "model_faces": len(faces),
        "two_sided_faces": sum(bool(selected_materials.get(m, {}).get('nocull'))
                               for m, _refs in faces),
        "vertex_count": len(vertices),
        "texture_triangles": dict(texture_triangles),
        "texture_images": {
            name: {"size": list(texture_images[name].size),
                   "rgb_sha256": hashlib.sha256(texture_images[name].tobytes()).hexdigest()}
            for name in sorted(texture_names)
        },
    }


def extract_models(rom: bytes, manifest: dict,
                   textures_by_model: dict[str, list[tuple[str, Image.Image]]] | None = None):
    if manifest.get("format") != 13 or manifest.get("rom_sha1") != hashlib.sha1(rom).hexdigest():
        raise ModelDecodeError("Model manifest does not match this ROM or format")
    result = []
    for recipe in manifest.get("models", []):
        if recipe.get("segment_id") != 7:
            raise ModelDecodeError("Only segment 7 is supported in this version")
        segment = mio0(rom, recipe["segment_rom_offset"])
        if recipe.get("display_list_pointers"):
            texture_rows = (textures_by_model or {}).get(recipe["model"], [])
            model_text, report = build_scene_model_text(segment, recipe, texture_rows)
        else:
            model_text, report = build_model_text(segment, recipe)
        report.update({"model": recipe["model"], "rom_sha1": hashlib.sha1(rom).hexdigest()})
        result.append((recipe["model"], model_text, report))
    return result


def main():
    parser = argparse.ArgumentParser(description="Rebuild the 3D title logo mesh from your own US ROM.")
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="Local output directory containing ROM-derived resources")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    rom = normalized_rom(args.rom.read_bytes())
    digest = hashlib.sha1(rom).hexdigest()
    if digest != ROM_SHA1:
        parser.error("Normalized ROM does not match the supported US ROM.")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    models = extract_models(rom, manifest)
    args.out.mkdir(parents=True, exist_ok=True)
    reports = []
    for model, model_text, report in models:
        filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", model).strip("_") + ".model.txt"
        (args.out / filename).write_text(model_text, encoding="utf-8")
        reports.append(report)
    report = {"format": 13, "rom_sha1": digest, "models": reports}
    (args.out / "informe_models.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, struct.error, json.JSONDecodeError) as error:
        print("ERROR:", error, file=sys.stderr)
        raise SystemExit(2)
