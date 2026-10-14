"""Preserve the ROM pipe camera and inherit its final pose into gameplay."""
from sm64_scratch.paths import METADATA_DIR
import json
import math
from pathlib import Path
from sm64_scratch.patches.patch_animation import Graph


def _blend_gameplay_focus(project):
    """Use the existing CLOSE polar transition for both position and focus."""
    targets = {target['name']: target for target in project['targets']}
    mario = targets['Mario']
    stage = next(t for t in project['targets'] if t['isStage'])
    g = Graph(mario, stage)
    definitions = [key for key, block in g.blocks.items()
                   if block['opcode'] == 'procedures_definition'
                   and g.blocks[block['inputs']['custom_block'][1]]['mutation']['proccode']
                   == 'INTRO | inherit pipe camera into gameplay']
    if len(definitions) != 1:
        raise ValueError('Unrecognized pipe handoff procedure')
    first = g.blocks[definitions[0]]['next']
    if g.blocks[first].get('mutation', {}).get('proccode') != 'SM64 CAMERA DECOMP2 | init BOB camera':
        raise ValueError('Unrecognized pipe handoff initialization')
    # Initialization already seeds Trans position/focus and PrevMario from
    # the final cinematic sample. A native CLOSE transition takes 30 ticks.
    g.insert_after(first, [g.set(g.var('CamSM64.TransitionFrames'), 30),
                           g.set(g.var('CamSM64.TransitionInit'), 1)])
    report = project['extensionData']['pipe_intro_camera_fix']
    report.update(version=2, handoff_transition_ticks=30,
                  handoff='Seed position and focus; blend both through the existing CLOSE polar transition')
    return report


def _native_pipe_placement(project):
    """Put the calibrated mesh in the same ROM unit system as its camera."""
    recipe = json.loads((METADATA_DIR / 'pipe.json').read_text())
    translation = recipe['affine_transform'][3]
    # Mesh conversion is 0.001 per ROM unit, camera conversion is 0.01.
    # Cancel the author's baked translation and place the native pipe at
    # (-1328, 260, 4664), mapped into the existing castle garden coordinates.
    scale = 10
    x, y, z = -translation[0] * scale, -translation[2] * scale, 3 + translation[1] * scale
    targets = {target['name']: target for target in project['targets']}
    world, mario = targets['World + Assets']['blocks'], targets['Mario']['blocks']
    def literal(blocks, key, slot, expected, value):
        current = blocks[key]['inputs'][slot]
        if not isinstance(current[1], list) or float(current[1][1]) != expected:
            raise ValueError('Unrecognized native pipe binding: ' + key)
        blocks[key]['inputs'][slot] = [1, [4, value]]
    literal(world, 'ke', 'wDjSt-+](8Kz!T1/?7dT', -.113256, x)
    literal(world, 'ke', '#DPsU*{gWl:,P3Nv{~c]', 3.056067, z)
    for key, slot in (('ke', 'ObQMR2fQ.;cwi8]1#.FL'), ('EV', 'ce+bdc4)$Rk+;kFcY=%u')):
        reporter = world[key]['inputs'][slot][1]
        literal(world, reporter, 'NUM1', .62106, y)
    for key in ('G{', 'G|', 'G}'):
        literal(world, key, 'ce+bdc4)$Rk+;kFcY=%u', 6.6, scale)
    literal(mario, 'ZU', 'VALUE', -1.5, -2)
    literal(mario, 'ultimate_b_48000014', 'NUM1', -1.5, -2)
    literal(mario, 'aqU', 'VALUE', -1.5, -2.1)
    literal(mario, 'awk', 'OPERAND2', -1.5, -2.1)
    literal(mario, 'auY', 'OPERAND2', -1.499, -2.099)
    report = project['extensionData']['pipe_intro_camera_fix']
    report.update(version=3, pipe_scale=scale, pipe_position=[x,y,z],
                  pipe_unit_system='100 ROM units per engine unit',
                  pipe_initial_offset=-2, pipe_final_offset=-2.1)
    return report


def _native_projection(project):
    """Keep the native 45 degree vertical frustum across the garden arrival.

    Scratch projects use horizontal FOV (240 / tan(fov / 2)). The N64
    perspective uses vertical FOV, so convert at the 480 x 360 stage aspect.
    The old 53..115 frame zoom was an authored approximation of the earlier
    cinematic. It must not run with the ROM's 271-sample camera spline.
    """
    render = next(t for t in project['targets'] if t['name'] == 'Render')
    blocks = render['blocks']
    horizontal = math.degrees(2 * math.atan((4 / 3) * math.tan(math.radians(45 / 2))))
    keys = ('v074_fov_ground_set', 'ultimate_b_49000024',
            'ultimate_b_49000005', 'ultimate_b_49000014')
    if any(key not in blocks for key in keys):
        raise ValueError('Unrecognized garden projection bindings')
    ground = blocks['v074_fov_ground_set']
    if float(ground['inputs']['VALUE'][1][1]) != 45:
        raise ValueError('Garden field of view changed; inspect the new source')
    ground['inputs']['VALUE'] = [1, [4, horizontal]]
    mario = next(t for t in project['targets'] if t['name'] == 'Mario')['blocks']
    for key, expected in (('ZU', -2), ('ZW', 1)):
        value = mario[key]['inputs']['VALUE'][1]
        if not isinstance(value, list) or float(value[1]) != expected:
            raise ValueError('Unrecognized pipe arrival state: ' + key)
        mario[key]['inputs']['VALUE'] = [1, [4, 0]]
    # Retain the sky's aspect conversion; remove only the obsolete FOV ramp.
    branch = blocks['ultimate_b_49000024']
    branch['inputs']['SUBSTACK'] = [2, 'ultimate_b_49000014']
    blocks['ultimate_b_49000014']['parent'] = 'ultimate_b_49000024'
    obsolete = set()
    def visit(key):
        if key == 'ultimate_b_49000014' or key in obsolete:
            return
        obsolete.add(key)
        block = blocks[key]
        for value in block.get('inputs', {}).values():
            for child in value[1:]:
                if isinstance(child, str) and child in blocks:
                    visit(child)
        if block.get('next'):
            visit(block['next'])
    visit('ultimate_b_49000005')
    for key in obsolete:
        del blocks[key]
    report = project['extensionData']['pipe_intro_camera_fix']
    report.update(version=4, garden_vertical_fov_degrees=45,
                  garden_horizontal_fov_degrees=horizontal,
                  obsolete_pipe_fov_ramp_removed=True,
                  pipe_initial_offset=0, pipe_initially_raised=True,
                  pipe_rise_timing='Native rise occurs during the preceding Lakitu flight, before this spline',
                  gameplay_camera_changes='Native garden frustum and pipe handoff; lobby camera retained')
    return report


def apply_pipe_camera_fixes(project):
    metadata = project.setdefault('extensionData', {})
    version = metadata.get('pipe_intro_camera_fix', {}).get('version', 0)
    if version == 4:
        return metadata['pipe_intro_camera_fix']
    if version == 3:
        return _native_projection(project)
    if version == 2:
        _native_pipe_placement(project)
        return _native_projection(project)
    if version == 1:
        _blend_gameplay_focus(project)
        _native_pipe_placement(project)
        return _native_projection(project)
    targets = {target['name']: target for target in project['targets']}
    mario, stage = targets['Mario'], next(t for t in project['targets'] if t['isStage'])
    g = Graph(mario, stage)
    required = ('ay;', 'aoK', '(C', '+f', 'amQ', 'cam77_b01491',
                'ultimate_b_49000005', 'ultimate_b_49000010')
    if any(key not in g.blocks for key in required):
        raise ValueError('Pipe camera bindings changed; inspect the new source before patching')
    pending = g.var('Intro.HandoffPending', 0)
    # Both the clock endpoint and inheritance index follow the actual tables.
    for key in ('ay;', 'aoK'):
        limit = g.op('subtract', g.length('Intro.Camera.X'), 1)
        g.blocks[key]['inputs']['OPERAND2'] = [3, limit, [10, '']]
        g.blocks[limit]['parent'] = key
    length = g.length('Intro.Camera.X')
    g.blocks['(C']['inputs']['VALUE'] = [3, length, [10, '']]
    g.blocks[length]['parent'] = '(C'
    # Synchronize emergence with the supplied native-spline implementation.
    candidates = []
    for key, block in g.blocks.items():
        if block['opcode'] != 'operator_lt':
            continue
        left = block.get('inputs', {}).get('OPERAND1', [None, None])[1]
        right = block.get('inputs', {}).get('OPERAND2', [None, None])[1]
        if (isinstance(left, list) and left[:2] == [12, 'Intro.Frame']
                and isinstance(right, list) and str(right[1]) == '53'):
            candidates.append(key)
    if len(candidates) != 1:
        raise ValueError('Unrecognized pipe emergence clock')
    g.blocks[candidates[0]]['inputs']['OPERAND2'] = [1, [4, '118']]
    # The native focus spline already follows Mario's jump. Only its vertical
    # floor-relative lower bound is dynamic; do not overwrite the Z channel.
    for key in ('ultimate_b_49000005', 'ultimate_b_49000010'):
        block = g.blocks[key]
        previous, following = block['parent'], block['next']
        if g.blocks[previous]['next'] != key:
            raise ValueError('Unrecognized pipe focus override')
        g.blocks[previous]['next'] = following
        g.blocks[following]['parent'] = previous
        dead = [key]
        for value in block['inputs'].values():
            if isinstance(value[1], str):
                dead.append(value[1])
        for identity in dead:
            del g.blocks[identity]
    g.insert_after('+f', [g.set(pending, 0)])
    g.insert_after('amQ', [g.set(pending, 1)])
    g.procedure('INTRO | inherit pipe camera into gameplay', [
        g.call('SM64 CAMERA DECOMP2 | init BOB camera'),
        g.set(g.var('CamSM64.SnapLakitu'), 0),
        g.set(g.var('CamSM64.PendingSnap'), 0),
        g.set(pending, 0),
    ])
    # This is the existing one-frame inheritance path. Seed all Lakitu goals
    # from its camera/focus, then disable only this handoff's forced snap.
    guard = g.control('control_if', g.op('and', g.op('equals', pending, 1),
                      g.op('equals', g.var('Level.ID'), 'GROUNDS')),
                      [g.call('INTRO | inherit pipe camera into gameplay')])
    g.insert_after('cam77_b01491', [guard])
    report = {'version': 1, 'camera_source': 'ROM US pipe-to-dialog spline',
              'sample_endpoint': 'length of Intro.Camera.X minus one',
              'emergence_wait_frame': 118, 'focus_z': 'native spline',
              'handoff': 'Seed existing CLOSE/Lakitu state from the final camera; no forced snap',
              'gameplay_camera_changes': 'Pipe handoff only; regular grounds and lobby camera retained'}
    metadata['pipe_intro_camera_fix'] = report
    _blend_gameplay_focus(project)
    _native_pipe_placement(project)
    return _native_projection(project)
