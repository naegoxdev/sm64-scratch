"""Derive grounded pose contact from decoded shoe vertices and skin poses."""
from sm64_scratch.paths import METADATA_DIR
import json
from pathlib import Path

LANDING_CLIPS = {17, 18, 75, 78, 147, 190, 192}
CROUCH_CLIPS = {150, 151, 152}  # stand up, crouch down, crouched idle
SEATED_SLIDE_CLIPS = {143, 145}  # stop slide, sliding on the bottom


def build_landing_contact_payload(model_text, runtime, reference_root_y):
    """Keep the lowest shoe sole on the local floor without changing joints.

    The Scratch pose loader strips root translation. Its existing contact table
    restores a common Y offset for all bones. Airborne clips remain untouched.
    """
    tokens = model_text.split()
    vertices = []
    for index, token in enumerate(tokens):
        if token == 'v':
            x, y, z = map(float, tokens[index + 1:index + 4])
            # Match ASSET | parse OBJ: vx=x, vy=z, vz=-y.
            vertices.append((x, z, -y))
    bones = runtime['Rig.VertexBone']
    if len(vertices) != len(bones):
        raise ValueError('Landing contact needs the actual model vertex order.')
    shoes = [(bone, vertex) for bone, vertex in zip(bones, vertices)
             if bone in (12, 15)]
    if not shoes or {bone for bone, _ in shoes} != {12, 15}:
        raise ValueError('Landing contact needs both shoe meshes.')
    body = list(zip(bones, vertices))
    seat = [(bone, vertex) for bone, vertex in body if bone == 1]
    if not seat:
        raise ValueError('Seated slide contact needs the pelvis mesh.')
    doc = json.loads((METADATA_DIR / 'mario_animation_aliases.json').read_text())
    aliases = {**doc['aliases'], **doc.get('extra_aliases', {})}
    names = {name for name, clip in aliases.items()
             if clip in LANDING_CLIPS | CROUCH_CLIPS | SEATED_SLIDE_CLIPS | {137}}
    poses = runtime['Rig.PoseData']
    offsets = list(runtime.get('Rig.GroundContactYOffset', [0.0] * (len(poses) // 180)))
    if len(offsets) != len(poses) // 180:
        raise ValueError('Contact table does not cover the pose frames.')
    initial = runtime.get('Rig.ActionInitialFrame', [0] * len(runtime['Rig.ActionName']))
    corrected = {}
    used = []
    crouch_used = []
    crouch_offsets = []
    slide_used = []
    slide_offsets = []
    for action, name in enumerate(runtime['Rig.ActionName']):
        if aliases.get(name) == 58:
            # Keep the authored seated impact: raised shoes are intentional.
            start = runtime['Rig.ActionStart'][action] - 1
            offsets[start:start + runtime['Rig.ActionCount'][action]] = [0.0] * runtime['Rig.ActionCount'][action]
            continue
        if name not in names:
            continue
        used.append(name)
        if aliases[name] in CROUCH_CLIPS:
            crouch_used.append(name)
        if aliases[name] in SEATED_SLIDE_CLIPS:
            slide_used.append(name)
        matrix_start = runtime['Rig.ActionMatrixStart'][action] - 1
        frame_start = runtime['Rig.ActionStart'][action] - 1
        for frame in range(min(initial[action], runtime['Rig.ActionCount'][action] - 1), runtime['Rig.ActionCount'][action]):
            matrix = poses[matrix_start + frame * 180:matrix_start + (frame + 1) * 180]
            root_dy = matrix[7] - reference_root_y
            # Seat the pelvis during the held slide. Shoe height is not the
            # support point in this pose. When getting up, contact transfers
            # to the lowest body surface, ending at the standing shoe soles.
            support = (seat if aliases[name] == 145 else
                       body if aliases[name] in {137, 143} else shoes)
            min_y = min(sum(matrix[(bone - 1) * 12 + 4 + axis] * vertex[axis]
                            for axis in range(3)) + matrix[(bone - 1) * 12 + 7] - root_dy
                        for bone, vertex in support)
            offsets[frame_start + frame] = -min_y
            corrected[frame_start + frame] = min_y
            if aliases[name] in CROUCH_CLIPS:
                crouch_offsets.append(-min_y)
            if aliases[name] in SEATED_SLIDE_CLIPS:
                slide_offsets.append(-min_y)
    report = dict(policy='Shoes for upright landings and crouch; pelvis surface for seated slide; lowest body surface for grounded dive and getting up; seated ground pound kept authored',
                  landing_aliases=[name for name in used if name not in crouch_used and name not in slide_used],
                  seated_slide_aliases=slide_used, seated_slide_contact_frames=len(slide_offsets),
                  seated_slide_y_offset_range=[min(slide_offsets, default=0),
                                              max(slide_offsets, default=0)],
                  crouch_aliases=crouch_used, crouch_contact_frames=len(crouch_offsets),
                  crouch_y_offset_range=[min(crouch_offsets, default=0),
                                         max(crouch_offsets, default=0)],
                  contact_frames=len(corrected),
                  highest_uncorrected_sole=max(corrected.values(), default=0),
                  lowest_uncorrected_sole=min(corrected.values(), default=0),
                  airborne_and_idle_policy='Existing offsets retained')
    return {'Rig.GroundContactYOffset': offsets}, report
