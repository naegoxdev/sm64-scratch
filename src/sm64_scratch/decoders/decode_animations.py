#!/usr/bin/env python3
"""Extract Mario, Butterfly, and Toad animation tables from a user-owned ROM.

The output contains game animation data and is written to a private temp folder.
Only the extractor belongs in a public porter package; never distribute output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import tempfile
from pathlib import Path

from sm64_scratch.rom_codec import ROM_SHA1, mio0, normalized_rom


# ROM positions of the actors' MIO0 streams and animation pointer tables.
# These are format descriptors only: no ROM bytes or extracted frames are kept here.
ACTORS = {
    "Butterfly": {
        "segment": 3,
        "rom_offset": 2102288,
        "table_offset": 0x56B0,
    },
    "Toad": {
        "segment": 6,
        "rom_offset": 1934096,
        "table_offset": 0xFC48,
    },
}

# In the supported US ROM, the linker places the Mario animation archive in
# the final ``assets`` segment.  Its first eight bytes are the DMA-table count
# and relocated source pointer; the entries then point at relative animation
# records inside the same archive.  This descriptor is an offset only: no
# animation samples are shipped in the porter.
MARIO_ANIMATION_TABLE_ROM_OFFSET = 0x4EC000
MARIO_ANIMATION_COUNT = 0xD1

ANIMATION_HEADER_SIZE = 0x18
MAX_ANIMATIONS = 64
MAX_TRACK_VALUES = 1_000_000


def read_u16_array(data: bytes, offset: int, count: int) -> list[int]:
    end = offset + 2 * count
    if offset < 0 or end > len(data):
        raise ValueError("Channel table is outside the MIO0 segment")
    return list(struct.unpack_from(">" + "H" * count, data, offset))


def read_s16_array(data: bytes, offset: int, count: int) -> list[int]:
    end = offset + 2 * count
    if offset < 0 or end > len(data):
        raise ValueError("Animation values are outside the MIO0 segment")
    return list(struct.unpack_from(">" + "h" * count, data, offset))


def segment_offset(pointer: int, segment: int) -> int:
    if pointer >> 24 != segment:
        raise ValueError(
            "Unexpected segment pointer: 0x%08X (expected segment %d)"
            % (pointer, segment)
        )
    return pointer & 0x00FFFFFF


def parse_animation(data: bytes, pointer: int, segment: int, clip_index: int) -> dict:
    header_offset = segment_offset(pointer, segment)
    if header_offset + ANIMATION_HEADER_SIZE > len(data):
        raise ValueError("Animation header is outside the segment")

    flags, y_divisor, start_frame, loop_start, loop_end, part_count, values_ptr, index_ptr, length = (
        struct.unpack_from(">6h3I", data, header_offset)
    )
    values_offset = segment_offset(values_ptr, segment)
    index_offset = segment_offset(index_ptr, segment)

    # Each joint group stores three (sample count, start index) channel pairs.
    pair_count = (part_count + 1) * 3
    if part_count < 0 or pair_count > 4096:
        raise ValueError("Animation part count is out of range")
    raw_index = read_u16_array(data, index_offset, pair_count * 2)
    channels = []
    required_values = 0
    for pair in range(pair_count):
        sample_count = raw_index[2 * pair]
        value_start = raw_index[2 * pair + 1]
        required_values = max(required_values, value_start + sample_count)
        channels.append({"sample_count": sample_count, "value_start": value_start})

    if required_values > MAX_TRACK_VALUES:
        raise ValueError("Animation value table exceeds bounds")
    values = read_s16_array(data, values_offset, required_values)

    return {
        "clip_index": clip_index,
        "segment_pointer": "0x%08X" % pointer,
        "flags": flags,
        "y_translation_divisor": y_divisor,
        "start_frame": start_frame,
        "loop_start": loop_start,
        "loop_end": loop_end,
        "part_count": part_count,
        "declared_length": length,
        "channels": channels,
        "values_s16": values,
    }


def parse_actor(rom: bytes, name: str, desc: dict) -> dict:
    rom_offset = desc["rom_offset"]
    segment_data = mio0(rom, rom_offset)
    table_offset = desc["table_offset"]
    animations = []

    for clip_index in range(MAX_ANIMATIONS):
        pointer_offset = table_offset + 4 * clip_index
        if pointer_offset + 4 > len(segment_data):
            raise ValueError(name + ": pointer table is outside the segment")
        pointer = struct.unpack_from(">I", segment_data, pointer_offset)[0]
        if pointer == 0:
            break
        animations.append(
            parse_animation(segment_data, pointer, desc["segment"], clip_index)
        )
    else:
        raise ValueError(name + ": pointer table does not terminate within bounds")

    if not animations:
        raise ValueError(name + ": no clips found in pointer table")
    return {
        "segment": desc["segment"],
        "segment_rom_offset": rom_offset,
        "clip_count": len(animations),
        "clips": animations,
    }


def parse_mario_archive(rom: bytes) -> dict:
    """Read the 209 Mario clips from the ROM's relative-offset DMA archive."""
    table = MARIO_ANIMATION_TABLE_ROM_OFFSET
    if table < 0 or table + 8 > len(rom):
        raise ValueError("Mario animation table is outside the ROM")

    clip_count, _relocated_source = struct.unpack_from(">II", rom, table)
    if clip_count != MARIO_ANIMATION_COUNT:
        raise ValueError(
            "Unexpected Mario table: %d clips (expected %d)"
            % (clip_count, MARIO_ANIMATION_COUNT)
        )
    entries_start = table + 8
    entries_end = entries_start + clip_count * 8
    if entries_end > len(rom):
        raise ValueError("Mario DMA entries are outside the ROM")

    animations = []
    for clip_index in range(clip_count):
        record_offset, record_size = struct.unpack_from(
            ">II", rom, entries_start + clip_index * 8
        )
        record_start = table + record_offset
        record_end = record_start + record_size
        if record_size < ANIMATION_HEADER_SIZE or record_end > len(rom):
            raise ValueError(
                "Mario record %d is outside the ROM: 0x%X + 0x%X"
                % (clip_index, record_start, record_size)
            )
        (flags, y_divisor, start_frame, loop_start, loop_end, part_count,
         values_relative, indices_relative, stored_size) = struct.unpack_from(
            ">6h3I", rom, record_start
        )
        if stored_size != record_size:
            raise ValueError(
                "Declared size of Mario clip %d does not match its DMA entry"
                % clip_index
            )
        if part_count < 0 or part_count > 4095:
            raise ValueError("Mario part count is out of range")

        pair_count = (part_count + 1) * 3
        indices_offset = record_start + indices_relative
        values_offset = record_start + values_relative
        if (indices_offset < record_start + ANIMATION_HEADER_SIZE
                or indices_offset + pair_count * 4 > record_end
                or values_offset < record_start + ANIMATION_HEADER_SIZE
                or values_offset >= record_end):
            raise ValueError("Mario channels %d are outside the record" % clip_index)

        raw_index = read_u16_array(rom, indices_offset, pair_count * 2)
        channels = []
        required_values = 0
        for pair in range(pair_count):
            sample_count = raw_index[pair * 2]
            value_start = raw_index[pair * 2 + 1]
            required_values = max(required_values, value_start + sample_count)
            channels.append({"sample_count": sample_count, "value_start": value_start})
        if required_values > MAX_TRACK_VALUES:
            raise ValueError("Mario value table exceeds bounds")
        if values_offset + required_values * 2 > record_end:
            raise ValueError("Mario values %d exceed the record" % clip_index)
        values = read_s16_array(rom, values_offset, required_values)

        animations.append({
            "clip_index": clip_index,
            "rom_record_offset": "0x%X" % record_start,
            "record_size": record_size,
            "flags": flags,
            "y_translation_divisor": y_divisor,
            "start_frame": start_frame,
            "loop_start": loop_start,
            "loop_end": loop_end,
            "frame_count": loop_end,
            "part_count": part_count,
            "channels": channels,
            "values_s16": values,
        })

    return {
        "archive_rom_offset": "0x%X" % table,
        "clip_count": len(animations),
        "clips": animations,
    }


