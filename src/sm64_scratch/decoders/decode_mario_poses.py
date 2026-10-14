#!/usr/bin/env python3
"""Generate Scratch's 15-bone Mario pose table from the user's ROM.

This module stores only animation decoding and the small rig layout needed by
the Scratch engine. Pose samples are read from the ROM at build time and are
inserted into the local SB3; they must not be published separately.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from sm64_scratch.decoders.decode_animations import parse_mario_archive
from sm64_scratch.decoders.decode_mario import MARIO_MODEL_SCALE


from sm64_scratch.paths import METADATA_DIR
ALIASES_PATH = METADATA_DIR / "mario_animation_aliases.json"
MARIO_WALKING_REFERENCE_CLIP = 72
MARIO_MATRIX_VALUES_PER_FRAME = 15 * 12
# Coordinate conversion used by the authored model exporter, not pose samples.
# Conjugate each skin transform by the export origin, then align the root.
EXPORT_SCALE = 0.9999900714440527
EXPORT_ORIGIN = (0.09460883203278009, -0.039399735534926594, -0.030658436431495937)
EXPORT_ROOT_OFFSET = (-0.1252607863405772, 0.12850406728393698, -0.01998576675704199)
CALIBRATED_REFERENCE_ROOT = (0.125262, 0.605933, 0.019986)

# The nodes follow mario_geo_body's animated-part tree. Mesh groups sometimes
# sit below a zero/offset pivot, so the 15 rendered bones point to nodes 1..19.
NODE_PARENT = [None, 0, 1, 2, 2, 4, 5, 6, 2, 8, 9, 10, 1, 12, 13, 14, 1, 16, 17, 18]
NODE_OFFSET = [
    (0, 0, 0),
    (0, 0, 0),          # 1 butt
    (68, 0, 0),         # 2 torso
    (87, 0, 0),         # 3 head
    (67, -10, 79),      # 4 left shoulder pivot
    (0, 0, 0),          # 5 left arm mesh
    (65, 0, 0),         # 6 left forearm
    (60, 0, 0),         # 7 left hand
    (68, -10, -79),     # 8 right shoulder pivot
    (0, 0, 0),          # 9 right arm mesh
    (65, 0, 0),         # 10 right forearm
    (60, 0, 0),         # 11 right hand
    (13, -8, 42),       # 12 left hip pivot
    (0, 0, 0),          # 13 left thigh mesh
    (89, 0, 0),         # 14 left lower leg
    (67, 0, 0),         # 15 left foot
    (13, -8, -42),      # 16 right hip pivot
    (0, 0, 0),          # 17 right thigh mesh
    (89, 0, 0),         # 18 right lower leg
    (67, 0, 0),         # 19 right foot
]
BONE_NODE = {
    1: 1, 2: 2, 3: 3,
    4: 5, 5: 6, 6: 7,
    7: 9, 8: 10, 9: 11,
    10: 13, 11: 14, 12: 15,
    13: 17, 14: 18, 15: 19,
}
IDLE_ACTION_NAMES = (
    "Other Standing Normal Part 1",
    "Other Standing Normal Part 2",
    "Other Standing Normal Part 3",
)


def _sample(clip: dict, channel_index: int, frame: int) -> int:
    channel = clip["channels"][channel_index]
    count = channel["sample_count"]
    if count < 1:
        return 0
    sample_index = min(frame, count - 1)
    return clip["values_s16"][channel["value_start"] + sample_index]


def _rotation_xyz(angles: tuple[int, int, int]) -> list[list[float]]:
    """Match mtxf_rotate_xyz_and_translate's rotation, in column form."""
    x, y, z = (angle * (2.0 * math.pi / 65536.0) for angle in angles)
    sx, cx = math.sin(x), math.cos(x)
    sy, cy = math.sin(y), math.cos(y)
    sz, cz = math.sin(z), math.cos(z)
    row_form = [
        [cy * cz, cy * sz, -sy],
        [sx * sy * cz - cx * sz, sx * sy * sz + cx * cz, sx * cy],
        [cx * sy * cz + sx * sz, cx * sy * sz - sx * cz, cx * cy],
    ]
    return [[row_form[column][row] for column in range(3)] for row in range(3)]


def _identity() -> list[list[float]]:
    return [[1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def _multiply(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[row][k] * b[k][column] for k in range(4))
             for column in range(4)] for row in range(4)]


def _translation(offset: tuple[int, int, int]) -> list[list[float]]:
    matrix = _identity()
    for axis, value in enumerate(offset):
        matrix[axis][3] = value * MARIO_MODEL_SCALE
    return matrix


def _rotation_matrix(clip: dict, node: int, frame: int) -> list[list[float]]:
    if node == 0:
        channels = (3, 4, 5)
    else:
        channels = tuple(6 + (node - 1) * 3 + axis for axis in range(3))
    rotation = _identity()
    values = _rotation_xyz(tuple(_sample(clip, channel, frame) for channel in channels))
    for row in range(3):
        for column in range(3):
            rotation[row][column] = values[row][column]
    return rotation


def _root_channel_values(clip: dict, frame: int) -> tuple[int, int, int]:
    # The authored Scratch clips import all three root channels. Applying the
    # native renderer's ANIM_FLAG translation filter here discards the door
    # clips' forward motion: their final position remains on the hinge and
    # walking immediately triggers the same door again. Preserve the imported
    # trajectory; the existing Scratch root-motion logic controls its use.
    return tuple(_sample(clip, channel, frame) for channel in range(3))


def _pose_for_clip(clip: dict, root_reference: tuple[float, float, float],
                   raw_reference: tuple[int, int, int]) -> list[list[float]]:
    """Return flattened 15 x 3x4 skin matrices for every ROM clip frame."""
    frame_count = clip["frame_count"]
    if frame_count < 1 or len(clip["channels"]) < 63:
        raise ValueError("Mario clip does not contain the expected 20 joint groups")

    pose_data: list[list[float]] = []
    rest_matrices = [None] * len(NODE_PARENT)
    rest_matrices[0] = _identity()
    for node in range(1, len(NODE_PARENT)):
        parent = NODE_PARENT[node]
        rest_matrices[node] = _multiply(rest_matrices[parent], _translation(NODE_OFFSET[node]))

    for frame in range(frame_count):
        raw_root = _root_channel_values(clip, frame)
        root = _identity()
        for axis in range(3):
            source_root = raw_root[axis]
            if axis == 1 and clip['clip_index'] in (9, 11):
                # Pole height is already applied by Tree.Handstand*YOffset.
                # The authored skin pose excludes the ROM root-Y channel.
                source_root = 0
            root[axis][3] = root_reference[axis] + (source_root - raw_reference[axis]) * MARIO_MODEL_SCALE
        root = _multiply(root, _rotation_matrix(clip, 0, frame))

        pose_matrices = [None] * len(NODE_PARENT)
        pose_matrices[0] = root
        for node in range(1, len(NODE_PARENT)):
            parent = NODE_PARENT[node]
            local = _multiply(_translation(NODE_OFFSET[node]), _rotation_matrix(clip, node, frame))
            pose_matrices[node] = _multiply(pose_matrices[parent], local)

        matrix_values = []
        for bone in range(1, 16):
            node = BONE_NODE[bone]
            inverse_rest = _identity()
            # The bind matrices contain translations only, so their inverse is
            # a direct negative offset chain; invert the composed translation.
            bind_position = [rest_matrices[node][axis][3] for axis in range(3)]
            for axis in range(3):
                inverse_rest[axis][3] = -bind_position[axis]
            skin = _multiply(pose_matrices[node], inverse_rest)
            for axis in range(3):
                skin[axis][3] = (EXPORT_SCALE * skin[axis][3] + EXPORT_ORIGIN[axis]
                                 - sum(skin[axis][j] * EXPORT_ORIGIN[j] for j in range(3))
                                 + EXPORT_ROOT_OFFSET[axis])
            for row in range(3):
                matrix_values.extend(skin[row][:4])
        pose_data.append([round(value, 7) if abs(value) > 0.00000005 else 0.0
                          for value in matrix_values])
    return pose_data


def collect_animation_requests(project: dict) -> set[str]:
    request_code = "ANIM | request %s loop %s speed %s"
    requested = set()
    for target in project.get("targets", []):
        for block in target.get("blocks", {}).values():
            if (block.get("opcode") != "procedures_call"
                    or block.get("mutation", {}).get("proccode") != request_code):
                continue
            argument_ids = json.loads(block["mutation"].get("argumentids", "[]"))
            if not argument_ids:
                continue
            input_value = block.get("inputs", {}).get(argument_ids[0])
            if not isinstance(input_value, list):
                continue
            for value in input_value[1:]:
                if (isinstance(value, list) and len(value) > 1
                        and value[0] == 10 and isinstance(value[1], str) and value[1]):
                    requested.add(value[1])
                    break
    return requested


def _stage_values(project: dict):
    stage = next((target for target in project.get("targets", [])
                  if target.get("isStage")), None)
    if stage is None:
        raise ValueError("BASE has no Stage target for the Mario rig")
    variables = {row[0]: row[1] for row in stage.get("variables", {}).values()}
    lists = {row[0]: row[1] for row in stage.get("lists", {}).values()}
    root = tuple(float(variables[name]) for name in
                 ("Rig.ReferenceRootX", "Rig.ReferenceRootY", "Rig.ReferenceRootZ"))
    if any(abs(value - expected) > 0.00001 for value, expected in zip(root, CALIBRATED_REFERENCE_ROOT)):
        raise ValueError("Updated BASE has a different Mario bind reference; recalibrate the rig conversion before building")
    return variables, lists, root


def build_mario_animation_payload(rom: bytes, base_project: dict, mario_model=None):
    aliases_doc = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    if aliases_doc.get("format") != 13:
        raise ValueError("Animation alias table is not in format 13")
    aliases = {**aliases_doc.get("aliases", {}), **aliases_doc.get("extra_aliases", {})}
    requested = collect_animation_requests(base_project)
    missing = sorted(requested - aliases.keys())
    if missing:
        raise ValueError("ANIM requests missing from mario_animation_aliases.json: "
                         + ", ".join(missing))

    clips = parse_mario_archive(rom)["clips"]
    if len(clips) != 209:
        raise ValueError("ROM does not contain the expected 209 Mario clips")
    by_id = {clip["clip_index"]: clip for clip in clips}
    if MARIO_WALKING_REFERENCE_CLIP not in by_id:
        raise ValueError("Mario walking reference clip was not found")
    walking = by_id[MARIO_WALKING_REFERENCE_CLIP]
    raw_reference = tuple(_sample(walking, channel, 0) for channel in range(3))
    _variables, base_lists, root_reference = _stage_values(base_project)

    # Reconstruct every mapped reference alias, including clips that are not
    # requested by a literal ANIM block in the current gameplay logic.
    # Two legacy aliases differ only in letter case. A casefold-only sort
    # retains the unordered set's tie order, changing clip indices across runs.
    action_names = ["1T-Pose"] + sorted(set(aliases) - {"1T-Pose"},
                                       key=lambda name: (name.casefold(), name))
    for required in IDLE_ACTION_NAMES:
        if required not in action_names:
            raise ValueError("Idle logic requires action " + required)
    clip_ids = [aliases[name] for name in action_names]
    for name, clip_id in zip(action_names, clip_ids):
        if clip_id not in by_id:
            raise ValueError(f"Alias {name!r} points to a missing clip: {clip_id}")

    pose_data: list[float] = []
    frame_start_by_clip: dict[int, int] = {}
    matrix_start_by_clip: dict[int, int] = {}
    total_frames = 0
    generated: dict[int, list[list[float]]] = {}
    for clip_id in dict.fromkeys(clip_ids):
        clip = by_id[clip_id]
        # The authored return uses the reversed start curve, while C has
        # a separate native return clip. Preserve the Scratch import policy.
        frames = (_pose_for_clip(by_id[11], root_reference, raw_reference)[::-1]
                  if clip_id == 12 else _pose_for_clip(clip, root_reference, raw_reference))
        if len(frames) != clip["frame_count"]:
            raise ValueError(f"Incorrect pose count for ROM clip {clip_id}")
        generated[clip_id] = frames
        frame_start_by_clip[clip_id] = total_frames + 1
        matrix_start_by_clip[clip_id] = len(pose_data) + 1
        for frame in frames:
            if len(frame) != MARIO_MATRIX_VALUES_PER_FRAME:
                raise ValueError("Pose does not contain 15 Scratch 3x4 matrices")
            pose_data.extend(frame)
        total_frames += len(frames)

    action_start = [frame_start_by_clip[clip_id] for clip_id in clip_ids]
    action_count = [by_id[clip_id]["frame_count"] for clip_id in clip_ids]
    action_matrix_start = [matrix_start_by_clip[clip_id] for clip_id in clip_ids]
    name_to_index = {name: index + 1 for index, name in enumerate(action_names)}
    default_name = "Other Standing Normal Part 1"
    default_clip = by_id[aliases[default_name]]
    local_frame_value = next((float(row[1]) for target in base_project["targets"]
                              for row in target.get("variables", {}).values()
                              if row[0] == "Anim.LocalFrame"), 1.0)
    default_frame = max(1, min(int(local_frame_value), default_clip["frame_count"]))
    default_pose_index = matrix_start_by_clip[default_clip["clip_index"]] - 1
    default_pose_index += (default_frame - 1) * MARIO_MATRIX_VALUES_PER_FRAME
    current_matrix = pose_data[default_pose_index:
                               default_pose_index + MARIO_MATRIX_VALUES_PER_FRAME]

    runtime = {
        "Rig.ActionName": action_names,
        "Rig.ActionStart": action_start,
        "Rig.ActionCount": action_count,
        "Rig.ActionMatrixStart": action_matrix_start,
        "Rig.ActionInitialFrame": [by_id[clip_id]['start_frame'] for clip_id in clip_ids],
        "Rig.PoseData": pose_data,
        "Rig.CurrentMatrix": current_matrix,
        "Rig.IdleClipID": [name_to_index[name] for name in IDLE_ACTION_NAMES],
        "Rig.IdleRightShoeMinY": [0.0] * sum(
            by_id[aliases[name]]["frame_count"] for name in IDLE_ACTION_NAMES),
        "Rig.IdleLeftShoeMinY": [0.0] * sum(
            by_id[aliases[name]]["frame_count"] for name in IDLE_ACTION_NAMES),
        "Rig.GroundContactYOffset": [0.0] * total_frames,
    }
    if mario_model is None:
        from sm64_scratch.decoders.decode_mario import build_mario_model
        mario_model = build_mario_model(rom)
    from sm64_scratch.decoders.decode_ground_contact import build_landing_contact_payload
    contact_runtime = {**runtime, **mario_model[3]}
    contact, contact_report = build_landing_contact_payload(
        mario_model[1], contact_runtime, root_reference[1])
    runtime.update(contact)
    scalars = {
        "Anim.ClipID": name_to_index[default_name],
        "Anim.MatrixStart": matrix_start_by_clip[default_clip["clip_index"]],
        "Anim.GlobalFrame": frame_start_by_clip[default_clip["clip_index"]] + default_frame - 1,
    }
    report = {
        "mario_animation_clips_from_rom": len(set(clip_ids)),
        "mario_animation_aliases": len(action_names),
        "mario_pose_frames_from_rom": total_frames,
        "mario_pose_matrix_values": len(pose_data),
        "mario_current_pose_frame": default_frame,
        "mario_requested_actions_found": len(requested),
        "mario_request_aliases_missing": [],
        "reference_alias_count": aliases_doc.get("reference_alias_count"),
        "unresolved_reference_aliases": aliases_doc.get("unresolved_reference_aliases", []),
        "translation_alignment": "Authored export-origin conjugation and root alignment applied; two pole aliases remain pending",
        "pole_conversion": "Root Y is external for clips 9/11; alias clip 12 reverses clip 11",
        "pending_pose_alignment_aliases": ["SM64 Pole Climb (decomp)", "Tree Pole Handstand"],
        "pending_import_frame_count_aliases": ["1T-Pose"],
        "animation_initial_frame_policy": "ROM start_frame preserved on clip entry; landing starts at frame 22",
        "root_motion_policy": "Standard clips retain all three ROM root channels, including door motion; pole handstand Y is applied separately",
        "landing_contact": contact_report,
    }
    return runtime, scalars, report


def build_mario_gameplay_payload(rom):
    # Scratch walks this native speed table in the reverse direction from C.
    import struct
    clips = parse_mario_archive(rom)['clips']
    by_id = {clip['clip_index']: clip for clip in clips}
    payload = {'Punch.ForwardVelocity': list(rom[0xE8D40:0xE8D48][::-1])}
    for name, clip_id, expected_count, reverse in (
            ('Tree.HandstandIdleYOffset', 9, 52, False),
            ('Tree.HandstandStartYOffset', 11, 17, False),
            ('Tree.HandstandReturnYOffset', 12, 17, True)):
        clip = by_id[clip_id]
        if clip['frame_count'] != expected_count:
            raise ValueError('Unexpected pole animation length: ' + name)
        # set_pole_position uses the animation's root-Y translation. The
        # authored engine stores it in model units and reverses the return clip.
        values = [round(_sample(clip, 1, frame) * .0025, 4)
                  for frame in range(expected_count)]
        payload[name] = values[::-1] if reverse else values

    # sTinyStarMovementParams in the supported US image: seven yaw/pitch pairs.
    # Scratch rounds the native s16 angles to one decimal degree.
    angles = struct.unpack_from('>14h', rom, 0xEB244)
    payload['BonkStar.YawOffset'] = [round(value * 360 / 65536, 1)
                                    for value in angles[::2]]
    payload['BonkStar.PitchParam'] = [round(value * 360 / 65536, 1)
                                     for value in angles[1::2]]
    # These are writable particle buffers, not ROM asset tables. The original
    # initializer replaces eight slots in place, which cannot grow an empty
    # Scratch list. It subsequently computes each particle velocity itself.
    for field in ('X', 'Y', 'Z', 'Size', 'VX', 'VY', 'VZ'):
        payload['GPStar.' + field] = [0] * 8
    return payload
