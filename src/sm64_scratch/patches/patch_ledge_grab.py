"""Add a small SM64-style ledge grab/climb path to the Scratch controller.

The geometry test follows mario_step.c at the project's 1:100 scale:
- after a descending wall hit, probe 0.60 units through the wall;
- search for a floor from current Y + 1.60;
- only grab if that floor is more than 1.00 units above Mario (the floor
  search naturally caps it at roughly 1.60 units);
- anchor Mario at the ledge floor and face back into the wall.

Animation mapping follows the ROM IDs:
- MARIO_ANIM_IDLE_ON_LEDGE       = 0x33 / 51
- MARIO_ANIM_FAST_LEDGE_GRAB     = 0x34 / 52
- MARIO_ANIM_SLOW_LEDGE_GRAB     = 0x00 / 0
"""
from __future__ import annotations

from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition, _call


def _target(project, name):
    return next(t for t in project['targets'] if t.get('name') == name)


def _or_many(g, values):
    values = list(values)
    if not values:
        raise ValueError('empty OR')
    out = values[0]
    for value in values[1:]:
        out = g.op('or', out, value)
    return out


def _literal(block, name):
    inp = block.get('inputs', {}).get(name)
    if not isinstance(inp, list):
        return None
    for item in inp[1:]:
        if isinstance(item, list) and len(item) > 1 and item[0] in (4, 10):
            return item[1]
    return None


def _owning_procedure(blocks, bid):
    cur = bid
    seen = set()
    while cur in blocks and cur not in seen:
        seen.add(cur)
        block = blocks[cur]
        if block.get('opcode') == 'procedures_definition':
            proto = block.get('inputs', {}).get('custom_block', [None, None])[1]
            if isinstance(proto, str) and proto in blocks:
                return blocks[proto].get('mutation', {}).get('proccode')
            return None
        cur = block.get('parent')
    return None


def _find_wall_hit_assignment(blocks):
    hits = []
    for bid, block in blocks.items():
        if block.get('opcode') != 'data_setvariableto':
            continue
        if block.get('fields', {}).get('VARIABLE', [None])[0] != 'SM64.StepResult':
            continue
        if _literal(block, 'VALUE') != 'AIR_STEP_HIT_WALL':
            continue
        if _owning_procedure(blocks, bid) == 'SM64 STEP | air quarter original %s %s %s':
            hits.append(bid)
    if len(hits) != 1:
        raise RuntimeError(f'Expected one original-quarter AIR_STEP_HIT_WALL assignment, found {hits}')
    return hits[0]


def apply_ledge_grab(project):
    """Port the SM64 mario_step.c ledge test without surface-type guesses.

    Native topology (scaled 1:100):
    * upper wall point probe at Y + 1.50, radius 0.50;
    * lower wall point probe at Y + 0.30, radius 0.50;
    * only consider a ledge when upperWall == NULL and lowerWall != NULL;
    * Mario must be descending and the wall push must oppose his velocity;
    * probe the floor 0.60 through the lower wall from Y + 1.60;
    * require that floor to be more than 1.00 above Mario.

    This is why fences/railings normally fail: either the upper probe still
    hits them, or there is no qualifying >100-unit floor behind the lower wall.
    There is deliberately no material/surface whitelist here.
    """
    ext = project.setdefault('extensionData', {})
    if ext.get('ledge_grab_v1'):
        return ext['ledge_grab_v1']

    stage = next(t for t in project['targets'] if t.get('isStage'))
    mario = _target(project, 'Mario')
    g = Graph(mario, stage)
    b = g.blocks

    action = g.var('SM64.Action')
    velx = g.var('SM64.VelX'); vely = g.var('SM64.VelY'); velz = g.var('SM64.VelZ')
    fvel = g.var('SM64.ForwardVel')
    px = g.var('SM64.PosX'); py = g.var('SM64.PosY'); pz = g.var('SM64.PosZ')
    render_y = g.var('SM64.RenderY')
    wall_found = g.var('SM64.WallFound'); wall_id = g.var('SM64.WallID')
    wall_nx = g.var('SM64.WallNX'); wall_nz = g.var('SM64.WallNZ')
    floor_found = g.var('SM64.FloorFound'); floor_id = g.var('SM64.FloorID')
    floor_h = g.var('SM64.FloorHeight'); floor_nx = g.var('SM64.FloorNX')
    floor_ny = g.var('SM64.FloorNY'); floor_nz = g.var('SM64.FloorNZ'); floor_type = g.var('SM64.FloorType')
    face_yaw = g.var('SM64.FaceYaw')
    grounded = g.var('SM64.Grounded'); landed = g.var('SM64.Landed')
    step_result = g.var('SM64.StepResult'); step_stopped = g.var('SM64.StepStopped')
    math_result = g.var('Math.Result')
    input_a = g.var('SM64.InputAPressed'); input_z = g.var('SM64.InputZPressed'); stick_y = g.var('SM64.StickY')
    action_timer = g.var('SM64.ActionTimer'); anim_end = g.var('Anim.End')
    sresx = g.var('Surface.ResultX'); sresz = g.var('Surface.ResultZ')

    top_x = g.var('Ledge.TopX'); top_y = g.var('Ledge.TopY'); top_z = g.var('Ledge.TopZ')
    ledge_nx = g.var('Ledge.WallNX'); ledge_nz = g.var('Ledge.WallNZ')
    test_x = g.var('Ledge.TestX'); test_z = g.var('Ledge.TestZ'); height = g.var('Ledge.Height')
    intended_x = g.var('Ledge.SourceIntendedX'); intended_y = g.var('Ledge.SourceIntendedY'); intended_z = g.var('Ledge.SourceIntendedZ')
    upper_found = g.var('Ledge.SourceUpperFound'); lower_found = g.var('Ledge.SourceLowerFound')
    next_x = g.var('Ledge.SourceNextX'); next_z = g.var('Ledge.SourceNextZ')
    lower_nx = g.var('Ledge.SourceWallNX'); lower_nz = g.var('Ledge.SourceWallNZ'); lower_id = g.var('Ledge.SourceWallID')
    disp_x = g.var('Ledge.SourceDispX'); disp_z = g.var('Ledge.SourceDispZ'); dot = g.var('Ledge.SourceDot')
    success_flag = g.var('Ledge.SourceSuccess')
    dynamic_hit = g.var('Ledge.SourceDynamicWallHit')
    gate_local_x = g.var('Ledge.GateLocalX')
    gate_local_z = g.var('Ledge.GateLocalZ')
    gate_query_x = g.var('Ledge.GateQueryX')
    gate_query_y = g.var('Ledge.GateQueryY')
    gate_query_z = g.var('Ledge.GateQueryZ')
    gate_query_radius = g.var('Ledge.GateQueryRadius')

    saved = {
        'ResultX': g.var('Ledge.SaveResultX'), 'ResultZ': g.var('Ledge.SaveResultZ'),
        'WallFound': g.var('Ledge.SaveWallFound'), 'WallID': g.var('Ledge.SaveWallID'),
        'WallNX': g.var('Ledge.SaveWallNX'), 'WallNZ': g.var('Ledge.SaveWallNZ'),
        'FloorFound': g.var('Ledge.SaveFloorFound'), 'FloorID': g.var('Ledge.SaveFloorID'),
        'FloorHeight': g.var('Ledge.SaveFloorHeight'), 'FloorNX': g.var('Ledge.SaveFloorNX'),
        'FloorNY': g.var('Ledge.SaveFloorNY'), 'FloorNZ': g.var('Ledge.SaveFloorNZ'),
        'FloorType': g.var('Ledge.SaveFloorType'),
    }

    # The native check needs intendedPos, so capture the existing quarter-step
    # procedure's x/y/z arguments before its normal wall resolution runs.
    def argument(name):
        return g.node('argument_reporter_string_number', fields={'VALUE': [name, None]})
    air_def = _definition(g, 'SM64 STEP | air quarter original %s %s %s')
    legacy_first = b[air_def].get('next')
    if not legacy_first:
        raise RuntimeError('air quarter original has no body')
    capture = [g.set(intended_x, argument('x')), g.set(intended_y, argument('y')), g.set(intended_z, argument('z'))]
    b[air_def]['next'] = g.chain(capture, air_def)
    b[capture[-1]]['next'] = legacy_first
    b[legacy_first]['parent'] = capture[-1]

    restore = [
        g.set(sresx, saved['ResultX']), g.set(sresz, saved['ResultZ']),
        g.set(wall_found, saved['WallFound']), g.set(wall_id, saved['WallID']),
        g.set(wall_nx, saved['WallNX']), g.set(wall_nz, saved['WallNZ']),
        g.set(floor_found, saved['FloorFound']), g.set(floor_id, saved['FloorID']),
        g.set(floor_h, saved['FloorHeight']), g.set(floor_nx, saved['FloorNX']),
        g.set(floor_ny, saved['FloorNY']), g.set(floor_nz, saved['FloorNZ']),
        g.set(floor_type, saved['FloorType']),
    ]
    g.procedure('SM64 LEDGE | restore source probe state', restore)

    # BOB's Chain Chomp gate is a dynamic collision object in the decomp, not
    # part of bob_seg7_collision_level.  The Scratch port renders the gate but
    # previously omitted bob_seg7_collision_chain_chomp_gate, so the native
    # upper-wall probe could see empty space and incorrectly treat the visual
    # fence as a ledge.  Reproduce that exact 1023x640-unit collision plane at
    # the level-script transform (1456,768,446), yaw 326 degrees.
    gate_cos = 0.8290375726
    gate_sin = -0.5591929035
    gate_probe = [
        g.set(dynamic_hit, 0),
        g.control('control_if',
                  g.op('and', g.op('equals', g.var('Level.ID'), 'BOB'),
                       g.op('equals', g.var('Chomp.GateBroken'), 0)), [
            g.set(gate_local_x,
                  g.op('subtract',
                       g.op('multiply', g.op('subtract', gate_query_x, 14.56), gate_cos),
                       g.op('multiply', g.op('subtract', gate_query_z, 4.46), gate_sin))),
            g.set(gate_local_z,
                  g.op('add',
                       g.op('multiply', g.op('subtract', gate_query_x, 14.56), gate_sin),
                       g.op('multiply', g.op('subtract', gate_query_z, 4.46), gate_cos))),
            g.control('control_if',
                      g.op('and',
                           g.op('and',
                                g.op('not', g.op('lt', gate_query_y, 7.68)),
                                g.op('not', g.op('gt', gate_query_y, 14.08))),
                           g.op('and',
                                g.op('lt', g.math('abs', gate_local_z), gate_query_radius),
                                g.op('and',
                                     g.op('not', g.op('lt', gate_local_x, -5.11)),
                                     g.op('not', g.op('gt', gate_local_x, 5.12))))),
                      [g.set(dynamic_hit, 1)])
        ])
    ]
    g.procedure('SM64 LEDGE | probe BOB chain gate', gate_probe)

    eligible_actions = _or_many(g, [
        g.op('equals', action, name) for name in (
            'ACT_JUMP', 'ACT_DOUBLE_JUMP', 'ACT_FREEFALL',
            'ACT_SIDE_FLIP', 'ACT_LONG_JUMP', 'ACT_WALL_KICK_AIR')
    ])

    success = [
        g.set(success_flag, 1),
        g.set(top_x, test_x), g.set(top_y, floor_h), g.set(top_z, test_z),
        g.set(ledge_nx, lower_nx), g.set(ledge_nz, lower_nz),
        # check_ledge_grab() copies ledgePos directly into m->pos.
        g.set(px, test_x), g.set(py, floor_h), g.set(pz, test_z), g.set(render_y, floor_h),
        g.set(velx, 0), g.set(vely, 0), g.set(velz, 0), g.set(fvel, 0),
        g.set(grounded, 0), g.set(landed, 0),
        g.set(wall_found, 1), g.set(wall_id, lower_id), g.set(wall_nx, lower_nx), g.set(wall_nz, lower_nz),
        _call(g, 'SM64 MATH | atan2 y %s x %s', [lower_nx, lower_nz]),
        g.set(face_yaw, g.op('mod', g.op('add', math_result, 180), 360)),
        _call(g, 'SM64 STATE | set action %s arg %s', ['ACT_LEDGE_GRAB', 0]),
        g.set(anim_end, 0),
        g.set(step_result, 'AIR_STEP_GRABBED_LEDGE'), g.set(step_stopped, 1),
    ]
    # Scratch has additional synthetic object walls (doors, cannon, boxes,
    # posts...) represented with negative WallID values. They are not entries
    # in the static Surface.* arrays and must never become ledge-grab sources.
    # Also reject the no-camera-collision wall classes used by thin fences and
    # railings. Compare the floor with the wall's local top, not the maximum
    # vertex height of the whole triangle. This preserves sloping terrain
    # ledges while still rejecting a railing above a platform.
    blocked_wall_type = _or_many(g, [
        g.op('equals', g.item('Surface.Type', lower_id), 'SURFACE_NO_CAM_COLLISION'),
        g.op('equals', g.item('Surface.Type', lower_id), 'SURFACE_NO_CAM_COLLISION_77'),
        g.op('equals', g.item('Surface.Type', lower_id), 'SURFACE_NO_CAM_COL_VERY_SLIPPERY'),
    ])
    static_solid_wall = g.op('and', g.op('gt', lower_id, 0), g.op('not', blocked_wall_type))
    solid_wall = g.var('Ledge.StaticSolidWall')
    wall_top = g.var('Ledge.LocalWallTopY')
    wall_t = g.var('Ledge.LocalWallT')
    edge_y = g.var('Ledge.LocalEdgeY')
    vertex_t = [g.var('Ledge.LocalVertexT' + str(i)) for i in range(1, 4)]
    vertex_y = [g.var('Ledge.LocalVertexY' + str(i)) for i in range(1, 4)]
    local_top_body = [
        g.set(wall_top, -10000),
        g.set(wall_t, g.op('subtract', g.op('multiply', lower_nx, next_z),
                          g.op('multiply', lower_nz, next_x))),
    ]
    for i in range(3):
        suffix = str(i + 1)
        local_top_body.extend([
            g.set(vertex_t[i], g.op('subtract',
                  g.op('multiply', lower_nx, g.item('Surface.Z' + suffix, lower_id)),
                  g.op('multiply', lower_nz, g.item('Surface.X' + suffix, lower_id)))),
            g.set(vertex_y[i], g.item('Surface.Y' + suffix, lower_id)),
        ])
    for a, c in ((0, 1), (1, 2), (2, 0)):
        between = g.op('or',
            g.op('and', g.op('not', g.op('lt', wall_t, vertex_t[a])),
                        g.op('not', g.op('gt', wall_t, vertex_t[c]))),
            g.op('and', g.op('not', g.op('lt', wall_t, vertex_t[c])),
                        g.op('not', g.op('gt', wall_t, vertex_t[a]))))
        local_top_body.append(g.control('control_if_else',
            g.op('gt', g.math('abs', g.op('subtract', vertex_t[c], vertex_t[a])), 0.000001), [
                g.control('control_if', between, [
                    g.set(edge_y, g.op('add', vertex_y[a], g.op('multiply',
                          g.op('divide', g.op('subtract', wall_t, vertex_t[a]),
                                        g.op('subtract', vertex_t[c], vertex_t[a])),
                          g.op('subtract', vertex_y[c], vertex_y[a])))),
                    g.control('control_if', g.op('gt', edge_y, wall_top), [g.set(wall_top, edge_y)]),
                ])
            ], [
                g.control('control_if', g.op('lt', g.math('abs',
                          g.op('subtract', wall_t, vertex_t[a])), 0.001), [
                    g.control('control_if', g.op('gt', vertex_y[a], wall_top), [g.set(wall_top, vertex_y[a])]),
                    g.control('control_if', g.op('gt', vertex_y[c], wall_top), [g.set(wall_top, vertex_y[c])]),
                ])
            ]))
    g.procedure('SM64 LEDGE | find local wall top', local_top_body)
    floor_matches_wall_top = g.op('lt',
        g.math('abs', g.op('subtract', floor_h, wall_top)), 0.35)
    floor_test = [
        g.set(test_x, g.op('subtract', next_x, g.op('multiply', lower_nx, 0.60))),
        g.set(test_z, g.op('subtract', next_z, g.op('multiply', lower_nz, 0.60))),
        _call(g, 'SURFACE | find floor x %s y %s z %s', [test_x, g.op('add', intended_y, 1.60), test_z]),
        g.set(height, g.op('subtract', floor_h, intended_y)),
        g.set(solid_wall, static_solid_wall),
        g.control('control_if', g.op('equals', solid_wall, 1), [g.call('SM64 LEDGE | find local wall top')]),
        g.control('control_if',
                  g.op('and',
                       g.op('and', g.op('equals', floor_found, 1), g.op('gt', height, 1.0)),
                       g.op('and', g.op('equals', solid_wall, 1), floor_matches_wall_top)),
                  success),
    ]
    displacement_test = [
        g.set(disp_x, g.op('subtract', next_x, intended_x)),
        g.set(disp_z, g.op('subtract', next_z, intended_z)),
        g.set(dot, g.op('add', g.op('multiply', disp_x, velx), g.op('multiply', disp_z, velz))),
        # Native code rejects only dot > 0.
        g.control('control_if', g.op('not', g.op('gt', dot, 0)), floor_test),
    ]
    save = [
        g.set(saved['ResultX'], sresx), g.set(saved['ResultZ'], sresz),
        g.set(saved['WallFound'], wall_found), g.set(saved['WallID'], wall_id),
        g.set(saved['WallNX'], wall_nx), g.set(saved['WallNZ'], wall_nz),
        g.set(saved['FloorFound'], floor_found), g.set(saved['FloorID'], floor_id),
        g.set(saved['FloorHeight'], floor_h), g.set(saved['FloorNX'], floor_nx),
        g.set(saved['FloorNY'], floor_ny), g.set(saved['FloorNZ'], floor_nz),
        g.set(saved['FloorType'], floor_type),
        g.set(success_flag, 0),
    ]
    probe = [
        # Scratch's resolver with height=0 evaluates one Y point: MidY=y and
        # lowerY <= y <= upperY, matching SM64's collisionData.y+offsetY test.
        _call(g, 'SURFACE | resolve walls x %s y %s z %s radius %s height %s',
              [intended_x, g.op('add', intended_y, 1.50), intended_z, 0.50, 0]),
        g.set(upper_found, wall_found), g.set(next_x, sresx), g.set(next_z, sresz),
        g.set(gate_query_x, intended_x), g.set(gate_query_y, g.op('add', intended_y, 1.50)),
        g.set(gate_query_z, intended_z), g.set(gate_query_radius, 0.50),
        g.call('SM64 LEDGE | probe BOB chain gate'),
        g.set(upper_found, g.op('or', g.op('equals', upper_found, 1), g.op('equals', dynamic_hit, 1))),
        _call(g, 'SURFACE | resolve walls x %s y %s z %s radius %s height %s',
              [next_x, g.op('add', intended_y, 0.30), next_z, 0.50, 0]),
        g.set(lower_found, wall_found), g.set(next_x, sresx), g.set(next_z, sresz),
        g.set(gate_query_x, next_x), g.set(gate_query_y, g.op('add', intended_y, 0.30)),
        g.set(gate_query_z, next_z), g.set(gate_query_radius, 0.50),
        g.call('SM64 LEDGE | probe BOB chain gate'),
        g.set(lower_found, g.op('or', g.op('equals', lower_found, 1), g.op('equals', dynamic_hit, 1))),
        g.set(lower_nx, wall_nx), g.set(lower_nz, wall_nz), g.set(lower_id, wall_id),
        g.control('control_if',
                  g.op('and',
                       g.op('and', g.op('equals', upper_found, 0), g.op('equals', lower_found, 1)),
                       g.op('and', g.op('not', g.op('gt', vely, 0)), eligible_actions)),
                  displacement_test),
        g.control('control_if', g.op('equals', success_flag, 0),
                  [g.call('SM64 LEDGE | restore source probe state')]),
    ]
    g.procedure('SM64 LEDGE | try grab', save + probe)

    # Run after the legacy body-wall result. The source-exact sub-probes decide
    # whether it is actually a ledge; otherwise original wall/floor state is restored.
    wall_hit = _find_wall_hit_assignment(g.blocks)
    g.insert_after(wall_hit, [g.call('SM64 LEDGE | try grab')])

    def pin_to_ledge():
        return [
            g.set(px, top_x), g.set(py, top_y), g.set(pz, top_z), g.set(render_y, top_y),
            g.set(velx, 0), g.set(vely, 0), g.set(velz, 0), g.set(fvel, 0),
            g.set(grounded, 0), g.set(landed, 0),
        ]

    def finish_climb():
        return [
            # Native climb_up_ledge() advances another 14 units in face yaw;
            # face yaw points into the wall, opposite its outward normal.
            g.set(px, g.op('subtract', top_x, g.op('multiply', ledge_nx, 0.14))),
            g.set(pz, g.op('subtract', top_z, g.op('multiply', ledge_nz, 0.14))),
            g.set(py, g.op('add', top_y, 0.01)), g.set(render_y, g.op('add', top_y, 0.01)),
            _call(g, 'SM64 STATE | set action %s arg %s', ['ACT_IDLE', 0]),
            g.set(anim_end, 0),
        ]

    # Positions use 1:100 world units; the existing velocity controller uses
    # native units. Match let_go_of_ledge(), including its outward placement.
    g.procedure('SM64 LEDGE | let go', [
        g.set(vely, 0), g.set(fvel, -8),
        g.set(px, g.op('subtract', px, g.op('multiply', 0.60, g.math('sin', face_yaw)))),
        g.set(pz, g.op('subtract', pz, g.op('multiply', 0.60, g.math('cos', face_yaw)))),
        _call(g, 'SURFACE | find floor x %s y %s z %s', [px, py, pz]),
        g.control('control_if_else', g.op('or', g.op('equals', floor_found, 0),
                  g.op('lt', floor_h, g.op('subtract', py, 1.0))),
                  [g.set(py, g.op('subtract', py, 1.0))], [g.set(py, floor_h)]),
        g.set(render_y, py),
        _call(g, 'SM64 STATE | set action %s arg %s', ['ACT_SOFT_BONK', 0]),
        g.set(anim_end, 0),
    ])
    support = g.var('Ledge.SupportValid')
    clearance = g.var('Ledge.HasClimbSpace')
    g.procedure('SM64 LEDGE | query support and clearance', [
        _call(g, 'SURFACE | find floor x %s y %s z %s', [top_x, g.op('add', top_y, 0.1), top_z]),
        g.set(support, g.op('and', g.op('equals', floor_found, 1),
              g.op('lt', g.math('abs', g.op('subtract', floor_h, top_y)), 0.1))),
        # Start just above the ledge floor, so low ceilings are not skipped.
        _call(g, 'SURFACE | find ceiling x %s y %s z %s', [top_x, g.op('add', top_y, 0.01), top_z]),
        g.set(clearance, g.op('or', g.op('equals', g.var('SM64.CeilFound'), 0),
              g.op('not', g.op('lt', g.var('SM64.CeilHeight'), g.op('add', top_y, 1.6))))),
    ])
    # Input mapping remains the project's keyboard adaptation. Clearance gates
    # both climb inputs; unsupported or steep ledges release Mario naturally.
    grab_body = pin_to_ledge() + [
        _call(g, 'ANIM | request %s loop %s speed %s', ['SM64 Idle On Ledge (decomp)', 1, 1]),
        g.call('SM64 LEDGE | query support and clearance'),
        g.control('control_if_else', g.op('or', g.op('equals', input_z, 1),
                  g.op('or', g.op('equals', support, 0), g.op('lt', floor_ny, 0.9063078))),
                  [g.call('SM64 LEDGE | let go')], [
            g.control('control_if', g.op('gt', action_timer, 3), [
                g.control('control_if_else', g.op('and', g.op('equals', input_a, 1),
                          g.op('equals', clearance, 1)), [
                    _call(g, 'SM64 STATE | set action %s arg %s', ['ACT_LEDGE_CLIMB_FAST', 0]),
                    g.set(anim_end, 0),
                ], [
                    g.control('control_if', g.op('and', g.op('gt', stick_y, 16),
                              g.op('equals', clearance, 1)), [
                        _call(g, 'SM64 STATE | set action %s arg %s', ['ACT_LEDGE_CLIMB_SLOW_1', 0]),
                        g.set(anim_end, 0),
                    ])
                ])
            ])
        ])
    ]
    fast_body = pin_to_ledge() + [
        g.call('SM64 LEDGE | query support and clearance'),
        g.control('control_if_else', g.op('equals', support, 0), [g.call('SM64 LEDGE | let go')], [
            _call(g, 'ANIM | request %s loop %s speed %s', ['SM64 Fast Ledge Grab (decomp)', 0, 1]),
            g.control('control_if', g.op('equals', anim_end, 1), finish_climb()),
        ]),
    ]
    slow_body = pin_to_ledge() + [
        g.call('SM64 LEDGE | query support and clearance'),
        g.control('control_if_else', g.op('equals', support, 0), [g.call('SM64 LEDGE | let go')], [
            _call(g, 'ANIM | request %s loop %s speed %s', ['SM64 Slow Ledge Grab (decomp)', 0, 1]),
            g.control('control_if', g.op('equals', anim_end, 1), finish_climb()),
        ]),
    ]
    g.procedure('SM64 LEDGE | update', [
        g.control('control_if_else', g.op('equals', action, 'ACT_LEDGE_GRAB'), grab_body, [
            g.control('control_if_else', g.op('equals', action, 'ACT_LEDGE_CLIMB_FAST'), fast_body, [
                g.control('control_if', g.op('equals', action, 'ACT_LEDGE_CLIMB_SLOW_1'), slow_body)
            ])
        ])
    ])

    auto_def = _definition(g, 'SM64 ACTION | automatic')
    legacy_auto = g.blocks[auto_def].get('next')
    if not legacy_auto:
        raise RuntimeError('SM64 ACTION | automatic has no body')
    is_ledge = _or_many(g, [
        g.op('equals', action, 'ACT_LEDGE_GRAB'),
        g.op('equals', action, 'ACT_LEDGE_CLIMB_FAST'),
        g.op('equals', action, 'ACT_LEDGE_CLIMB_SLOW_1'),
    ])
    wrapper = g.node('control_if_else', {'CONDITION': is_ledge})
    ledge_call = g.call('SM64 LEDGE | update')
    g.blocks[wrapper]['inputs']['SUBSTACK'] = [2, g.chain([ledge_call], wrapper)]
    g.blocks[wrapper]['inputs']['SUBSTACK2'] = [2, legacy_auto]
    g.blocks[legacy_auto]['parent'] = wrapper
    g.blocks[wrapper]['parent'] = auto_def
    g.blocks[auto_def]['next'] = wrapper

    report = {
        'version': 7,
        'source': 'n64decomp/sm64 src/game/mario_step.c',
        'scratch_safety': {'static_wall_only': True, 'exclude_no_cam_fences': True, 'floor_top_tolerance': 0.35},
        'wall_top_filter': 'interpolate the triangle edge heights at the local wall tangent coordinate',
        'climb_clearance': 'fresh ceiling query above the ledge floor; at least 1.60 world units',
        'drop_policy': 'native -8 forward velocity; move outward 0.60, resolve floor, lower up to 1.00; ACT_SOFT_BONK',
        'excluded_air_actions': ['ACT_TRIPLE_JUMP', 'ACT_BACKFLIP'],
        'upper_probe': {'offset_y': 1.50, 'radius': 0.50},
        'lower_probe': {'offset_y': 0.30, 'radius': 0.50},
        'topology_gate': 'upperWall == NULL && lowerWall != NULL',
        'floor_probe': {'through_wall': 0.60, 'search_above': 1.60, 'minimum_rise': 1.00},
        'surface_whitelist': False,
        'dynamic_collision_restored': 'BOB Chain Chomp Gate: bob_seg7_collision_chain_chomp_gate at (1456,768,446), yaw 326',
        'actions': ['ACT_LEDGE_GRAB', 'ACT_LEDGE_CLIMB_FAST', 'ACT_LEDGE_CLIMB_SLOW_1'],
        'animations': {
            'hang': ['SM64 Idle On Ledge (decomp)', 51],
            'fast_climb': ['SM64 Fast Ledge Grab (decomp)', 52],
            'slow_climb': ['SM64 Slow Ledge Grab (decomp)', 0],
        },
        'drop_input': 'Z', 'fast_climb_input': 'A', 'slow_climb_input': 'stick forward',
    }
    ext['ledge_grab_v1'] = report
    return report
