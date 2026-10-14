#!/usr/bin/env python3
"""Rebuild Mario's normal body model and rig index arrays from a local US ROM.

This module ships only decoding code and small format descriptors. Model
geometry and texture pixels are read from the ROM passed to the builder.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter

from PIL import Image

from sm64_scratch.decoders.decode_models import ModelDecodeError, _fmt, _material_tokens, _texture_tokens, read_intro_logo
from sm64_scratch.rom_codec import decode_rgba16, mio0


MARIO_SEGMENT_ROM_OFFSET = 0x114750
MARIO_MODEL_SCALE = 0.0038252

# Each pointer is an ordinary Mario body display list in segment 4. Bone IDs
# are the indices already consumed by Rig.VertexBone and Rig.FaceBone.
MARIO_PARTS = [
    (1, 0x0400CC98, "butt", 0),
    (2, 0x04010370, "torso", 0),
    (3, 0x040119A0, "head", 4),
    (4, 0x0400D1D8, "left_arm", 1),
    (5, 0x0400D2F8, "left_forearm", 1),
    (6, 0x0400D8F0, "left_hand", 2),
    (7, 0x0400DDE8, "right_arm", 1),
    (8, 0x0400DF08, "right_forearm", 1),
    (9, 0x0400E458, "right_hand", 2),
    (10, 0x0400E7B0, "left_thigh", 0),
    (11, 0x0400E918, "left_leg", 0),
    (12, 0x0400ECA0, "left_foot", 3),
    (13, 0x0400EFB8, "right_thigh", 0),
    (14, 0x0400F1D8, "right_leg", 0),
    (15, 0x0400F4E8, "right_foot", 3),
]

# Bind-space origins in the Scratch model's axes. The first value is the
# project's model origin; the remaining offsets correspond to the 15 body
# joints. They contain no ROM payload and are used only to place ROM vertices
# around the joints expected by the existing Scratch rig.
MARIO_BIND_ORIGINS = [
    (0.09461, 0.03066, -0.03944),
    (0.35472, 0.03066, -0.03944),
    (0.68751, 0.03066, -0.03944),
    (0.61100, -0.27153, -0.07769),
    (0.85964, -0.27153, -0.07769),
    (1.08915, -0.27153, -0.07769),
    (0.61483, 0.33285, -0.07769),
    (0.86347, 0.33285, -0.07769),
    (1.09298, 0.33285, -0.07769),
    (0.14434, -0.13000, -0.07004),
    (0.48478, -0.13000, -0.07004),
    (0.74106, -0.13000, -0.07004),
    (0.14434, 0.19132, -0.07004),
    (0.48478, 0.19132, -0.07004),
    (0.74106, 0.19132, -0.07004),
]

MARIO_TEXTURES = {
    0x04001090: ("Mario_Button_Tex", "Mario_Button_Mat"),
    0x04001890: ("Mario_Logo_Tex", "Mario_Logo_Mat"),
    0x04002090: ("Mario_Sideburn_Tex", "Mario_Sideburn_Mat"),
    0x04002890: ("Mario_Mustache_Tex", "Mario_Mustache_Mat"),
    0x04003090: ("Mario_Eyes_Tex", "Mario_Eyes_Mat"),
}

MARIO_LIGHT_RGB = {
    0: (0, 0, 255),
    1: (255, 0, 0),
    2: (255, 255, 255),
    3: (114, 28, 14),
    4: (254, 193, 121),
    5: (115, 6, 0),
}


def build_mario_model(rom: bytes):
    """Return the +Mario model, its rig index lists, and ROM validation data."""
    segment = mio0(rom, MARIO_SEGMENT_ROM_OFFSET)
    from sm64_scratch.decoders.decode_atlas import build_mario_atlas
    textures = {"MarioAtlas": build_mario_atlas(rom)}
    atlas_origins = {0x04001090: (0, 0), 0x04001890: (64, 0),
                     0x04002090: (128, 64), 0x04002890: (192, 64),
                     0x04003090: (128, 0)}
    solid_coords = {0: (.5, 63.5), 1: (64.5, .5), 2: (.5, 255.5),
                    3: (.5, 191.5), 4: (128.5, .5), 5: (64.5, 191.5)}

    vertex_index = {}
    uv_index = {}
    vertices = []
    texcoords = []
    normals = []
    vertex_bones = []
    face_bones = []
    faces = []
    face_counts = Counter()
    texture_counts = Counter()

    for bone, pointer, _part_name, default_light in MARIO_PARTS:
        triangles, _commands = read_intro_logo(segment, pointer, set(MARIO_TEXTURES), 4)
        for face_ordinal, triangle in enumerate(triangles):
            texture_pointer = triangle["texture_pointer"]
            light = triangle["light_group"]
            if light is None:
                light = default_light
            texture_name, material = "MarioAtlas", "MarioMaterial"
            if texture_pointer is not None and texture_pointer not in atlas_origins:
                raise ModelDecodeError(f"Mario texture has no atlas descriptor: {texture_pointer:#010x}")
            ordered = [triangle["vertices"][index] for index in (0, 2, 1)]
            refs = []
            points = []
            st_scale = 1024.0  # all five loaded Mario tiles are 32 x 32
            for vertex in ordered:
                position = tuple(
                    vertex["position"][axis] * MARIO_MODEL_SCALE
                    + MARIO_BIND_ORIGINS[bone - 1][axis]
                    for axis in range(3)
                )
                key = (bone, position)
                if key not in vertex_index:
                    index = len(vertices) + 1
                    vertex_index[key] = index
                    vertices.append(position)
                    vertex_bones.append(bone)
                vertex_id = vertex_index[key]
                points.append(position)

                if texture_pointer is None:
                    # Small authored material corrections refer to ROM triangle
                    # ordinals; no geometry or texture pixels are embedded.
                    if bone == 2 and face_ordinal in (99, 100, 102, 103, 104, 107):
                        light = 0
                    if bone == 3 and face_ordinal in (40, 41, 42, 43):
                        x, y = 192.5, 64.5
                    elif bone == 3 and face_ordinal == 99:
                        x, y = solid_coords[5]
                    else:
                        x, y = solid_coords[light]
                    uv = (round(x / 256, 5), round(y / 1024, 5))
                else:
                    x, y = atlas_origins[texture_pointer]
                    u, v = (max(0, min(1, value / 1024)) for value in vertex["st"])
                    if texture_pointer == 0x04001090:
                        # Button tile wraps across its seam, including its
                        # lower edge at T=0 in the authored atlas conversion.
                        u = (vertex["st"][0] % 1024) / 1024
                        v = (vertex["st"][1] % 1024 or 1024) / 1024
                    uv = (round((x + .5 + u * 63) / 256, 5),
                          round((y + .5 + v * 63) / 1024, 5))
                uv_key = (texture_name, uv)
                if uv_key not in uv_index:
                    uv_index[uv_key] = len(texcoords) + 1
                    texcoords.append(uv)
                refs.append((vertex_id, uv_index[uv_key]))

            first, second, third = points
            edge1 = tuple(second[i] - first[i] for i in range(3))
            edge2 = tuple(third[i] - first[i] for i in range(3))
            normal = (
                edge1[1] * edge2[2] - edge1[2] * edge2[1],
                edge1[2] * edge2[0] - edge1[0] * edge2[2],
                edge1[0] * edge2[1] - edge1[1] * edge2[0],
            )
            length = math.sqrt(sum(value * value for value in normal))
            if length == 0:
                raise ModelDecodeError("Mario mesh contains a degenerate face")
            normal = tuple(round(-value / length, 6) for value in normal)
            normals.append(normal)
            faces.append((material, refs, len(normals)))
            face_bones.append(bone)
            face_counts[bone] += 1
            texture_counts[texture_name] += 1

    expected_faces = [72, 118, 190, 36, 28, 46, 36, 28, 46, 30, 20, 26, 30, 20, 26]
    actual_faces = [face_counts[index] for index in range(1, 16)]
    if actual_faces != expected_faces:
        raise ModelDecodeError("The 15 Mario parts did not produce the expected mesh: "
                               + repr(actual_faces))
    if len(vertex_bones) != len(vertices) or len(face_bones) != len(faces):
        raise ModelDecodeError("Bone indices are not aligned with the Mario mesh")

    tokens = ["o", "Mario_Rigged"]
    tokens.extend("v " + " ".join(_fmt(value) for value in point) for point in vertices)
    tokens.extend("vt " + " ".join(_fmt(value) for value in uv) for uv in texcoords)
    tokens.extend("vn " + " ".join(_fmt(value) for value in normal) for normal in normals)
    for name, image in sorted(textures.items()):
        tokens.extend(_texture_tokens(name, image).split())
    material_texture = {"MarioMaterial": "MarioAtlas"}
    for material, texture in sorted(material_texture.items()):
        tokens.extend(_material_tokens(material, texture).split())
    active_material = None
    for material, refs, normal_index in faces:
        if material != active_material:
            tokens.extend(("usemtl", material))
            active_material = material
        tokens.append("f " + " ".join(
            f"{vertex}/{uv}/{normal_index}" for vertex, uv in refs))

    model_text = " ".join(tokens)
    report = {
        "model": "Mario",
        "rom_sha1": hashlib.sha1(rom).hexdigest(),
        "rom_segment_offset": MARIO_SEGMENT_ROM_OFFSET,
        "rom_triangles": len(faces),
        "model_faces": len(faces),
        "vertex_count": len(vertices),
        "uv_count": len(texcoords),
        "bone_vertex_counts": dict(Counter(vertex_bones)),
        "bone_face_counts": dict(face_counts),
        "texture_images": {
            name: {"size": list(image.size),
                   "rgb_sha256": hashlib.sha256(image.tobytes()).hexdigest()}
            for name, image in sorted(textures.items())
        },
        "texture_triangle_counts": dict(texture_counts),
    }
    runtime = {"Rig.VertexBone": vertex_bones, "Rig.FaceBone": face_bones}
    return "Mario", model_text, report, runtime
