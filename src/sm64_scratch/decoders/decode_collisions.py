#!/usr/bin/env python3
"""Rebuild the Bob-omb Battlefield collision tables from a user-owned ROM.

Only format descriptors and conversion code live here. Collision coordinates
and derived surfaces are read from the ROM at build time and inserted into the
local SB3; the extracted arrays must not be distributed separately.
"""
from __future__ import annotations

import math
import struct

from sm64_scratch.rom_codec import mio0


BOB_SEGMENT_ROM_OFFSET = 4_178_608
BOB_COLLISION_POINTER = 0x0700E958
GROUNDS_SEGMENT_ROM_OFFSET = 4_500_416
GROUNDS_COLLISION_POINTER = 0x0700F3A0
COL_INIT = 0x0040
COL_TRI_STOP = 0x0041
COL_END = 0x0042
COL_SPECIAL_INIT = 0x0043
COL_WATER_BOX_INIT = 0x0044

SURFACE_NAMES = {
    0x0000: "SURFACE_DEFAULT",
    0x0001: "SURFACE_BURNING",
    0x0004: "SURFACE_0004",
    0x0005: "SURFACE_HANGABLE",
    0x0009: "SURFACE_SLOW",
    0x000A: "SURFACE_DEATH_PLANE",
    0x000B: "SURFACE_CLOSE_CAMERA",
    0x000D: "SURFACE_WATER",
    0x000E: "SURFACE_FLOWING_WATER",
    0x0012: "SURFACE_INTANGIBLE",
    0x0013: "SURFACE_VERY_SLIPPERY",
    0x0014: "SURFACE_SLIPPERY",
    0x0015: "SURFACE_NOT_SLIPPERY",
    0x0016: "SURFACE_TTM_VINES",
    0x001A: "SURFACE_MGR_MUSIC",
    **{value: "SURFACE_INSTANT_WARP_%02X" % value for value in range(0x1B, 0x1F)},
    **{value: name for value, name in {
        0x21: "SURFACE_SHALLOW_QUICKSAND", 0x22: "SURFACE_DEEP_QUICKSAND",
        0x23: "SURFACE_INSTANT_QUICKSAND", 0x24: "SURFACE_DEEP_MOVING_QUICKSAND",
        0x25: "SURFACE_SHALLOW_MOVING_QUICKSAND", 0x26: "SURFACE_QUICKSAND",
        0x27: "SURFACE_MOVING_QUICKSAND",
    }.items()},
    0x0028: "SURFACE_WALL_MISC",
    0x0029: "SURFACE_NOISE_DEFAULT",
    0x002A: "SURFACE_NOISE_SLIPPERY",
    0x002C: "SURFACE_HORIZONTAL_WIND",
    0x002D: "SURFACE_INSTANT_MOVING_QUICKSAND",
    0x002E: "SURFACE_ICE",
    0x002F: "SURFACE_LOOK_UP_WARP",
    0x0030: "SURFACE_HARD",
    0x0032: "SURFACE_WARP",
    0x0033: "SURFACE_TIMER_START",
    0x0034: "SURFACE_TIMER_END",
    0x0035: "SURFACE_HARD_SLIPPERY",
    0x0036: "SURFACE_HARD_VERY_SLIPPERY",
    0x0037: "SURFACE_HARD_NOT_SLIPPERY",
    0x0038: "SURFACE_VERTICAL_WIND",
    0x0065: "SURFACE_BOSS_FIGHT_CAMERA",
    0x0066: "SURFACE_CAMERA_FREE_ROAM",
    0x0068: "SURFACE_THI3_WALLKICK",
    0x0069: "SURFACE_CAMERA_8_DIR",
    0x006E: "SURFACE_CAMERA_MIDDLE",
    0x006F: "SURFACE_CAMERA_ROTATE_RIGHT",
    0x0070: "SURFACE_CAMERA_ROTATE_LEFT",
    0x0072: "SURFACE_CAMERA_BOUNDARY",
    0x0073: "SURFACE_NOISE_VERY_SLIPPERY_73",
    0x0074: "SURFACE_NOISE_VERY_SLIPPERY_74",
    0x0075: "SURFACE_NOISE_VERY_SLIPPERY",
    0x0076: "SURFACE_NO_CAM_COLLISION",
    0x0077: "SURFACE_NO_CAM_COLLISION_77",
    0x0078: "SURFACE_NO_CAM_COL_VERY_SLIPPERY",
    0x0079: "SURFACE_NO_CAM_COL_SLIPPERY",
    0x007A: "SURFACE_SWITCH",
    0x007B: "SURFACE_VANISH_CAP_WALLS",
    **{value: "SURFACE_PAINTING_WOBBLE_%02X" % value for value in range(0xA6, 0xD3)},
    **{value: "SURFACE_PAINTING_WARP_%02X" % value for value in range(0xD3, 0xFD)},
    0x00FD: "SURFACE_WOBBLING_WARP",
    0x00FF: "SURFACE_TRAPDOOR",
}

# These surface groups carry a fourth per-triangle word in the N64 collision
# stream. The Scratch runtime does not currently simulate the force value, but
# must skip it to stay aligned with later groups.
SURFACE_FORCE_TYPES = {0x0004, 0x000E, 0x0024, 0x0025, 0x0027, 0x002C, 0x002D}


class CollisionDecodeError(ValueError):
    pass


def parse_collision(segment: bytes, segmented_pointer: int = BOB_COLLISION_POINTER,
                    force_types: set[int] | None = None):
    """Read the level's COL_VERTEX/COL_TRI stream from its decompressed segment."""
    if segmented_pointer >> 24 not in (6, 7):
        raise CollisionDecodeError("Collision pointer does not address segment 6 or 7")
    offset = segmented_pointer & 0x00FFFFFF

    def read_u16() -> int:
        nonlocal offset
        if offset + 2 > len(segment):
            raise CollisionDecodeError("Truncated collision command")
        value = struct.unpack_from(">H", segment, offset)[0]
        offset += 2
        return value

    if read_u16() != COL_INIT:
        raise CollisionDecodeError("COL_INIT was not found in the ROM")
    vertex_count = read_u16()
    if not 1 <= vertex_count <= 0xFFFF or offset + vertex_count * 6 > len(segment):
        raise CollisionDecodeError("Vertex count is out of range")
    vertices = [struct.unpack_from(">hhh", segment, offset + index * 6)
                for index in range(vertex_count)]
    offset += vertex_count * 6

    triangles = []
    water_boxes = []
    stopped = False
    while offset + 2 <= len(segment):
        surface_type = read_u16()
        if surface_type in (COL_TRI_STOP, COL_END):
            stopped = True
            break
        if surface_type not in SURFACE_NAMES:
            raise CollisionDecodeError(
                "Unsupported surface type: 0x%04X" % surface_type)
        triangle_count = read_u16()
        carries_force = surface_type in (force_types or SURFACE_FORCE_TYPES)
        stride = 8 if carries_force else 6
        if offset + triangle_count * stride > len(segment):
            raise CollisionDecodeError("Truncated triangle group")
        for _ in range(triangle_count):
            a, b, c = struct.unpack_from(">HHH", segment, offset)
            offset += stride
            if max(a, b, c) >= vertex_count:
                raise CollisionDecodeError("Triangle index is outside the vertex table")
            triangles.append((surface_type, a, b, c))

    if not stopped or not triangles:
        raise CollisionDecodeError("Collision stream did not terminate correctly")
    if surface_type == COL_TRI_STOP:
        water_boxes = _find_water_boxes(segment, offset)
    return vertices, triangles, water_boxes


def _find_water_boxes(segment: bytes, start: int):
    """Locate the optional environmental-box table following COL_TRI_STOP."""
    for command_offset in range(start, len(segment) - 4, 2):
        if struct.unpack_from(">H", segment, command_offset)[0] != COL_WATER_BOX_INIT:
            continue
        count = struct.unpack_from(">H", segment, command_offset + 2)[0]
        if not 1 <= count <= 20:
            continue
        end = command_offset + 4 + count * 12
        if end + 2 > len(segment) or struct.unpack_from(">H", segment, end)[0] != COL_END:
            continue
        rows = []
        valid = True
        for index in range(count):
            values = struct.unpack_from(">hhhhhh", segment, command_offset + 4 + index * 12)
            box_id, x1, z1, x2, z2, level = values
            if box_id != index or x1 >= x2 or z1 >= z2:
                valid = False
                break
            rows.append({"BoxID": box_id, "X1": x1 / 100, "Z1": z1 / 100,
                         "X2": x2 / 100, "Z2": z2 / 100, "Level": level / 100})
        if valid:
            return rows
    return []


def _triangle_values(vertices, triangle):
    surface_type, ia, ib, ic = triangle
    points = [tuple(component / 100 for component in vertices[index])
              for index in (ia, ib, ic)]
    p1, p2, p3 = points
    ab = tuple(p2[axis] - p1[axis] for axis in range(3))
    ac = tuple(p3[axis] - p1[axis] for axis in range(3))
    normal = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    magnitude = math.sqrt(sum(component * component for component in normal))
    if magnitude == 0:
        raise CollisionDecodeError("ROM contains a degenerate triangle")
    normal = tuple(round(component / magnitude, 8) for component in normal)
    return {
        "X1": p1[0], "Y1": p1[1], "Z1": p1[2],
        "X2": p2[0], "Y2": p2[1], "Z2": p2[2],
        "X3": p3[0], "Y3": p3[1], "Z3": p3[2],
        "NX": normal[0], "NY": normal[1], "NZ": normal[2],
        "Origin": round(-sum(normal[axis] * p1[axis] for axis in range(3)), 8),
        "LowerY": min(point[1] for point in points),
        "UpperY": max(point[1] for point in points),
        "Type": SURFACE_NAMES[surface_type],
        "MinX": min(point[0] for point in points),
        "MaxX": max(point[0] for point in points),
        "MinZ": min(point[2] for point in points),
        "MaxZ": max(point[2] for point in points),
    }


def _variable_values(project):
    stage = next((target for target in project.get("targets", [])
                  if target.get("isStage")), None)
    if stage is None:
        raise CollisionDecodeError("BASE.sb3 has no Stage target")
    return {row[0]: row[1] for row in stage.get("variables", {}).values()}


def build_collision_payload(rom: bytes, project: dict, *, segment_rom_offset: int,
                            collision_pointer: int, list_prefix: str,
                            force_types: set[int] | None = None,
                            grid: dict | None = None,
                            world_translation=(0, 0, 0), authored_walls=()):
    """Rebuild a level's static collision and cell index from its ROM segment."""
    segment = mio0(rom, segment_rom_offset)
    vertices, triangles, water_boxes = parse_collision(segment, collision_pointer, force_types)
    rows = [_triangle_values(vertices, triangle) for triangle in triangles]
    post_triangles = 0
    if list_prefix == 'Level.BOB':
        # Native wooden posts have dynamic collision in C. These four static
        # decorative/reward posts use the same stream at their macro locations.
        from sm64_scratch.decoders.decode_bob_expansion import macro_rows
        post_vertices, post_faces, _ = parse_collision(mio0(rom, 1851952), 0x06002490)
        for preset, yaw, x, y, z, parameter in macro_rows(rom):
            if preset != 106:
                continue
            angle = math.radians(yaw)
            transformed = [(x + .5 * (px * math.cos(angle) + pz * math.sin(angle)),
                            y + .5 * py,
                            z + .5 * (pz * math.cos(angle) - px * math.sin(angle)))
                           for px, py, pz in post_vertices]
            rows.extend(_triangle_values(transformed, face) for face in post_faces)
            post_triangles += len(post_faces)
    dx, dy, dz = world_translation
    for row in rows:
        for key in ('X1', 'X2', 'X3', 'MinX', 'MaxX'):
            row[key] = round(row[key] + dx, 8)
        for key in ('Y1', 'Y2', 'Y3', 'LowerY', 'UpperY'):
            row[key] = round(row[key] + dy, 8)
        for key in ('Z1', 'Z2', 'Z3', 'MinZ', 'MaxZ'):
            row[key] = round(row[key] + dz, 8)
        row['Origin'] = round(-sum(row['N' + a] * row[a + '1'] for a in 'XYZ'), 8)
    for box in water_boxes:
        for key, delta in [('X1', dx), ('X2', dx), ('Z1', dz), ('Z2', dz), ('Level', dy)]:
            box[key] = round(box[key] + delta, 8)
    # The Scratch reference closes the submerged metal door with a procedural
    # rectangle. Its dimensions are authored collision calibration, not ROM data.
    for wall in authored_walls:
        x, y, z = wall['origin']; w, h = wall['width'], wall['height']
        points = [(x, y, z), (x + w, y, z), (x + w, y + h, z), (x, y + h, z)]
        raw = [tuple(v * 100 for v in p) for p in points]
        rows.extend(_triangle_values(raw, (0, *indices)) for indices in [(0, 1, 2), (0, 2, 3)])

    variables = _variable_values(project)
    try:
        if grid is None:
            origin_x = float(variables["Collision.GridMinX"])
            origin_z = float(variables["Collision.GridMinZ"])
            cell_size = float(variables["Collision.GridCellSize"])
            width = int(variables["Collision.GridWidth"])
        else:
            origin_x = float(grid["min_x"])
            origin_z = float(grid["min_z"])
            cell_size = float(grid["cell_size"])
            width = int(grid["width"])
    except (KeyError, TypeError, ValueError) as error:
        raise CollisionDecodeError("Invalid collision grid") from error
    if cell_size <= 0 or not 1 <= width <= 64:
        raise CollisionDecodeError("Invalid collision grid in BASE.sb3")

    # A one-unit border keeps edge triangles in neighbouring candidate cells.
    # The Scratch floor/wall tests do the final exact triangle-point check.
    border = float((grid or {}).get("border", 1.0))
    tolerance = float((grid or {}).get("boundary_tolerance", 0.0))
    if border < 0 or tolerance < 0:
        raise CollisionDecodeError("Grid border and tolerance must be nonnegative")
    cell_start = []
    cell_count = []
    cell_surface = []
    for z_cell in range(width):
        for x_cell in range(width):
            x_min = origin_x + x_cell * cell_size
            x_max = x_min + cell_size
            z_min = origin_z + z_cell * cell_size
            z_max = z_min + cell_size
            indices = [index + 1 for index, row in enumerate(rows)
                       if row["MinX"] <= x_max + border + tolerance
                       and row["MaxX"] >= x_min - border - tolerance
                       and row["MinZ"] <= z_max + border + tolerance
                       and row["MaxZ"] >= z_min - border - tolerance]
            cell_start.append(len(cell_surface) + 1)
            cell_count.append(len(indices))
            cell_surface.extend(indices)

    payload = {list_prefix + ".Surface." + key: [row[key] for row in rows]
               for key in ("X1", "Y1", "Z1", "X2", "Y2", "Z2", "X3", "Y3", "Z3",
                           "NX", "NY", "NZ", "Origin", "LowerY", "UpperY", "Type",
                           "MinX", "MaxX", "MinZ", "MaxZ")}
    payload.update({
        list_prefix + ".Collision.CellStart": cell_start,
        list_prefix + ".Collision.CellCount": cell_count,
        list_prefix + ".Collision.CellSurface": cell_surface,
    })
    if water_boxes:
        for key in ("BoxID", "X1", "Z1", "X2", "Z2", "Level"):
            payload[list_prefix + ".Water." + key] = [row[key] for row in water_boxes]
    return payload, {
        "segment_rom_offset": segment_rom_offset,
        "collision_pointer": "0x%08X" % collision_pointer,
        "vertex_count": len(vertices),
        "surface_count": len(rows),
        "wooden_post_triangles": post_triangles,
        "world_translation": list(world_translation),
        "authored_wall_triangles": 2 * len(authored_walls),
        "grid_width": width,
        "grid_cells": width * width,
        "grid_border": border,
        "grid_surface_references": len(cell_surface),
        "water_box_count": len(water_boxes),
    }


def build_bob_collision_payload(rom: bytes, project: dict):
    """Compatibility wrapper for Bob-omb Battlefield."""
    return build_collision_payload(
        rom, project, segment_rom_offset=BOB_SEGMENT_ROM_OFFSET,
        collision_pointer=BOB_COLLISION_POINTER, list_prefix="Level.BOB")


def build_grounds_collision_payload(rom: bytes, project: dict):
    """Rebuild the Castle Grounds collision that supports the game's start area."""
    return build_collision_payload(
        rom, project, segment_rom_offset=GROUNDS_SEGMENT_ROM_OFFSET,
        collision_pointer=GROUNDS_COLLISION_POINTER, list_prefix="Level.Grounds",
        grid={"min_x": -130, "min_z": -130, "cell_size": 30, "width": 8})
