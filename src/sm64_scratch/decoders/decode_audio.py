#!/usr/bin/env python3
"""Rebuild title audio from a local US ROM.

Decode M64 programs, CTL/TBL banks and VADPCM without decompilation files or
reference WAVs. Voices retain exact mono samples. Stereo music and effects use
ROM notes, loops, tuning and envelopes; mixing approximates RSP resampling and
reverb and is not a bit-exact console recording.
"""
from __future__ import annotations

import math
import struct
import wave
from array import array


ROM_SOUND_CTL = (5748512, 97856)       # 0x57B720, US v1.0
ROM_SOUND_TBL = (5846368, 2216704)     # 0x593560, US v1.0
TITLE_AUDIO_DISPLAY_NAMES = {
    'title_music_intro': 'SM64 Title Music Intro (ROM)',
    'title_music_loop': 'SM64 Title Music Loop (ROM)',
    'logo_sound': "SM64 Logo Coin + It's-a-me (ROM)",
    'logo_coin': 'SM64 Logo Coin (ROM)',
    'coin': 'SM64 Coin (ROM)',
    'logo_voice': "SM64 It's-a-me Mario (ROM)",
    'hello_voice': 'SM64 Mario Hello (ROM)',
    'hand_appear': 'SM64 Hand Appear (ROM)',
    'hand_disappear': 'SM64 Hand Disappear (ROM)',
    'face_pinch': 'SM64 Face Pinch (ROM)',
    'face_release': 'SM64 Face Release (ROM)',
    'start_star': 'SM64 Start Star (ROM)',
    'camera_zoom_in': 'SM64 Camera Zoom In (ROM)',
    'camera_zoom_out': 'SM64 Camera Zoom Out (ROM)',
}
# Direct one-shot voice samples used by the US title screen.  The sample
# descriptors are located from the ROM bank tables; the numeric instruments
# are the ROM instrument indexes (the JSON bank names are only development
# references and are not read by the public builder).
TITLE_ONE_SHOTS = {
    "logo_voice": {"bank": 10, "instrument": 8, "sample_rate": 16000,
                   "sound_id": "0x70140080"},
    "hello_voice": {"bank": 8, "instrument": 18, "sample_rate": 16000,
                     "sound_id": "0x2432FF80"},
}


def _u8(data, pos):
    return data[pos], pos + 1


def _u16(data, pos):
    return struct.unpack_from(">H", data, pos)[0], pos + 2


def _var(data, pos):
    value, pos = _u8(data, pos)
    if value & 0x80:
        value = ((value << 8) & 0x7F00) | data[pos]
        pos += 1
    return value, pos


def _seq_entries(data, offset, size, magic):
    blob = data[offset:offset + size]
    got, count = struct.unpack_from(">HH", blob, 0)
    if got != magic:
        raise ValueError(f"Unexpected sound header: {got:#x}")
    return [struct.unpack_from(">II", blob, 4 + i * 8) for i in range(count)]


def _bank_data(rom, bank_index):
    ctl_off, ctl_size = ROM_SOUND_CTL
    tbl_off, tbl_size = ROM_SOUND_TBL
    ctl_entries = _seq_entries(rom, ctl_off, ctl_size, 1)
    tbl_entries = _seq_entries(rom, tbl_off, tbl_size, 2)
    if bank_index >= len(ctl_entries) or bank_index >= len(tbl_entries):
        raise ValueError(f"Sound bank out of bounds: {bank_index:#x}")
    co, cs = ctl_entries[bank_index]
    to, ts = tbl_entries[bank_index]
    raw = rom[ctl_off + co:ctl_off + co + cs]
    if len(raw) < 16:
        raise ValueError("Truncated CTL bank")
    num_instruments, num_drums, _shared = struct.unpack_from(">III", raw, 0)
    # The US CTL entry has a 16-byte header; pointers below are relative to it.
    ctl = raw[16:]
    tbl = rom[tbl_off + to:tbl_off + to + ts]
    instruments = []
    for index in range(num_instruments):
        ptr = struct.unpack_from(">I", ctl, 4 + index * 4)[0]
        if ptr == 0:
            instruments.append(None)
            continue
        loaded, low, high, release, envelope = struct.unpack_from(">BBBBI", ctl, ptr)
        sounds = []
        for n in range(3):
            sample_ptr, tuning = struct.unpack_from(">If", ctl, ptr + 8 + n * 8)
            sounds.append(None if sample_ptr == 0 else (sample_ptr, float(tuning)))
        instruments.append({"low": low, "high": high, "sounds": sounds,
                            "release": release, "envelope": envelope})

    # The first pointer in an AudioBank is the table of percussion pointers.
    # US sequence channels select percussion with chan_setinstr 127; the N64
    # player then indexes this table by the note value. Ignoring it turns the
    # title rhythm and several intro attacks into missing or unrelated notes.
    drums = []
    drum_table = struct.unpack_from(">I", ctl, 0)[0] if len(ctl) >= 4 else 0
    for index in range(num_drums):
        if not drum_table or drum_table + index * 4 + 4 > len(ctl):
            drums.append(None)
            continue
        ptr = struct.unpack_from(">I", ctl, drum_table + index * 4)[0]
        if ptr == 0 or ptr + 12 > len(ctl):
            drums.append(None)
            continue
        release, pan, _loaded, _pad = struct.unpack_from(">BBBB", ctl, ptr)
        sample_ptr, tuning = struct.unpack_from(">If", ctl, ptr + 4)
        drums.append({
            "release": release,
            "envelope": struct.unpack_from(">I", ctl, ptr + 12)[0],
            "pan": pan,
            "sound": None if sample_ptr == 0 else (sample_ptr, float(tuning)),
        })
    return ctl, tbl, instruments, drums


def _sample_descriptor(ctl, sample_ptr):
    # US entries use: zero, tbl address, loop pointer, codebook pointer, size.
    _zero, address, _loop, book, size = struct.unpack_from(">IIIII", ctl, sample_ptr)
    if size % 9 == 1:
        size -= 1
    order, predictors = struct.unpack_from(">ii", ctl, book)
    if order != 2 or predictors != 2:
        raise ValueError("Unsupported N64 VADPCM codebook")
    raw = struct.unpack_from(">" + str(16 * order * predictors) + "h",
                             ctl, book + 8)
    table = []
    for predictor in range(predictors):
        rows = [[0] * (order + 8) for _ in range(8)]
        for j in range(order):
            for k in range(8):
                rows[k][j] = raw[predictor * order * 8 + j * 8 + k]
        for k in range(1, 8):
            rows[k][order] = rows[k - 1][order - 1]
        rows[0][order] = 1 << 11
        for k in range(1, 8):
            for j in range(k):
                rows[j][k + order] = 0
            for j in range(k, 8):
                rows[j][k + order] = rows[j - k][order]
        table.append(rows)
    return address, size, table


def _decode_sample(ctl, tbl, sample_ptr):
    address, size, table = _sample_descriptor(ctl, sample_ptr)
    state = [0] * 16
    output = array("h")
    for frame_offset in range(0, size, 9):
        frame = tbl[address + frame_offset:address + frame_offset + 9]
        if len(frame) != 9:
            break
        header = frame[0]
        scale = 1 << (header >> 4)
        predictor = header & 0x0F
        nibbles = []
        for value in frame[1:]:
            nibbles.extend((value >> 4, value & 0x0F))
        nibbles = [(n - 16 if n >= 8 else n) * scale for n in nibbles]
        for part in range(2):
            history = state[14:16] if part == 0 else state[6:8]
            vector = history + nibbles[part * 8:part * 8 + 8]
            for i in range(8):
                row = table[predictor][i]
                dot = sum(row[k] * vector[k] for k in range(2 + i))
                # C's inner_product rounds negative values down.
                value = dot // (1 << 11) + nibbles[part * 8 + i]
                value = max(-32768, min(32767, value))
                state[part * 8 + i] = value
                output.append(value)
    return output


# Sequence format interpreter, written for the US bytecode. The ROM remains the
# source of the score, sound-effect programs, bank data, envelopes and loops.
ROM_SEQUENCES = 0x7B0860


def _signed(v):
    return v - 256 if v >= 128 else v


class _Script:
    def __init__(self, address):
        self.pc = address
        self.wake = 0
        self.stack = []
        self.value = 0
        self.active = True


class _Channel(_Script):
    def __init__(self, address, bank):
        super().__init__(address)
        self.bank = bank
        self.instrument = 0
        self.large = False
        self.transpose = 0
        self.volume = 1.0
        self.volscale = 1.0
        self.pan = 64
        self.panweight = 1.0
        self.freq = 1.0
        self.envelope = None
        self.release = None
        self.reverb = 0
        self.vibrate = 32
        self.vibextent = 0
        self.layers = {}
        self.controls = []
        self.io = {0: -1, 1: 0}


class _Layer(_Script):
    def __init__(self, address):
        super().__init__(address)
        self.transpose = 0
        self.instrument = None
        self.pan = 64
        self.duration = 128
        self.velocity = 127
        self.last_delay = 1
        self.default_delay = 1
        self.portamento = None
        self.continuous = False
        self.note = None


class _Sequence:
    def __init__(self, data, bank, sfx_address=None, variation=0, sfx_sound_id=None):
        self.data = bytearray(data)
        self.main = _Script(0)
        self.bank = bank
        self.tempo = 120.0
        self.volume = 1.0
        self.transpose = 0
        # play_music sets seqVariation from the requested sequence ID before
        # loading it: normal title 0x02 -> 0, Game Over 0x82 -> signed -128.
        # The engine's startup value (-1) is replaced at this point.
        self.variation = _signed(variation & 255)
        self.channels = {}
        self.events = []
        self.times = []
        self.jumps = []
        self.address_ticks = {}
        self.sfx = sfx_address is not None
        if self.sfx:
            self.main.active = False
            ch = _Channel(sfx_address, bank)
            ch.large = True
            if sfx_sound_id is not None:
                # external.c supplies the requested sound index in channel IO 4.
                # The red-coin program reads it to index its native major scale.
                ch.io[4] = (sfx_sound_id >> 16) & 255
            self.channels[7] = ch

    def u8(self, s):
        v = self.data[s.pc]
        s.pc += 1
        return v

    def u16(self, s):
        hi = self.u8(s)
        return hi * 256 + self.u8(s)

    def var(self, s):
        v = self.u8(s)
        return ((v & 127) * 256 + self.u8(s)) if v & 128 else v

    def flow(self, s, op, tick):
        if op == 0xFF:
            if s.stack:
                s.pc = s.stack.pop()[0]
            else:
                s.active = False
            return True
        if op in (0xFC, 0xFB, 0xFA, 0xF9, 0xF5):
            dest = self.u16(s)
            branch = op in (0xFC, 0xFB) or (op == 0xFA and s.value == 0) or \
                     (op == 0xF9 and s.value < 0) or (op == 0xF5 and s.value >= 0)
            if branch:
                if op == 0xFC:
                    s.stack.append([s.pc, None])
                if s is self.main and op == 0xFB:
                    self.jumps.append((tick, dest))
                s.pc = dest
            return True
        if op == 0xF8:
            count = self.u8(s) or 256
            s.stack.append([s.pc, count])
            return True
        if op == 0xF7:
            s.stack[-1][1] -= 1
            if s.stack[-1][1]:
                s.pc = s.stack[-1][0]
            else:
                s.stack.pop()
            return True
        if op == 0xF6:
            s.stack.pop()
            return True
        return False

    def sequence(self, tick):
        s = self.main
        for _ in range(2000):
            if not s.active or s.wake > tick:
                return
            self.address_ticks.setdefault(s.pc, tick)
            op = self.u8(s)
            if self.flow(s, op, tick):
                continue
            if op in (0xFD, 0xFE):
                s.wake = tick + (max(1, self.var(s)) if op == 0xFD else 1)
                return
            if 0x90 <= op <= 0x9F:
                index, address = op & 15, self.u16(s)
                old = self.channels.get(index)
                if old:
                    self.end_layers(old, tick)
                    old.pc, old.stack, old.wake, old.active = address, [], tick, True
                else:
                    self.channels[index] = _Channel(address, self.bank)
            elif op == 0x80:
                s.value = self.variation
            elif op == 0x70:
                self.variation = _signed(s.value & 255)
            elif op == 0x50:
                s.value -= self.variation
            elif op == 0xDD:
                self.tempo = max(1, self.u8(s))
            elif op == 0xDC:
                self.tempo = max(1, self.tempo + _signed(self.u8(s)))
            elif op == 0xDB:
                self.volume = self.u8(s) / 127.0
            elif op == 0xDA:
                self.volume += _signed(self.u8(s)) / 127.0
            elif op in (0xDF, 0xDE):
                self.transpose = (self.transpose if op == 0xDE else 0) + _signed(self.u8(s))
            elif op in (0xD7, 0xD6, 0xD2, 0xD1):
                mask = self.u16(s)
                if op == 0xD6:
                    for index, ch in self.channels.items():
                        if mask & (1 << index):
                            ch.active = False
                            self.end_layers(ch, tick)
            elif op == 0xCC:
                s.value = _signed(self.u8(s))
            elif op in (0xD3, 0xD5, 0xD0, 0xF2):
                self.u8(s)
            elif op in (0xF1, 0xD4):
                pass
            else:
                raise ValueError(f"Unsupported M64 sequence command {op:#x} at {s.pc-1:#x}")
        raise ValueError('M64 loop without a delay')

    def end_layers(self, ch, tick):
        for l in ch.layers.values():
            if l.note:
                l.note['end'] = min(l.note['end'], tick)
        ch.layers.clear()

    def channel(self, ch, tick):
        for _ in range(2000):
            if not ch.active or ch.wake > tick:
                return
            op = self.u8(ch)
            if self.flow(ch, op, tick):
                if not ch.active and not self.sfx:
                    self.end_layers(ch, tick)
                continue
            if op in (0xFD, 0xFE):
                ch.wake = tick + (max(1, self.var(ch)) if op == 0xFD else 1)
                return
            if 0x90 <= op <= 0x9F:
                index, address = op & 15, self.u16(ch)
                old = ch.layers.get(index)
                if old and old.note:
                    old.note['end'] = min(old.note['end'], tick)
                ch.layers[index] = _Layer(address)
                ch.layers[index].wake = tick
            elif 0xA0 <= op <= 0xAF:
                l = ch.layers.pop(op & 15, None)
                if l and l.note:
                    l.note['end'] = min(l.note['end'], tick)
            elif op == 0xC1:
                ch.instrument = self.u8(ch)
                ch.envelope = None
                ch.release = None
            elif op in (0xC3, 0xC4):
                ch.large = op == 0xC4
            elif op == 0xC6:
                ch.bank = self.u8(ch)  # sound player's US bank set is 00..0A
            elif op == 0xDF:
                ch.volume = self.u8(ch) / 127.0
            elif op == 0xE0:
                ch.volscale = self.u8(ch) / 128.0
            elif op == 0xDD:
                ch.pan = self.u8(ch)
            elif op == 0xDC:
                ch.panweight = self.u8(ch) / 128.0
            elif op == 0xDB:
                ch.transpose = _signed(self.u8(ch))
            elif op == 0xDE:
                ch.freq = self.u16(ch) / 32768.0
            elif op == 0xD3:
                ch.freq = 2.0 ** (_signed(self.u8(ch)) / 127.0)
            elif op == 0xDA:
                ch.envelope = self.u16(ch)
            elif op == 0xD9:
                ch.release = self.u8(ch)
            elif op == 0xD4:
                ch.reverb = self.u8(ch)
            elif op == 0xD8:
                ch.vibextent = self.u8(ch)
            elif op == 0xD7:
                ch.vibrate = self.u8(ch)
            elif op in (0xE1, 0xE2):
                start, target, duration = self.u8(ch), self.u8(ch), self.u8(ch)
                # Retain endpoints and transition length for diagnostics.
                ch.controls.append((tick, op, start, target, duration))
                if op == 0xE1:
                    ch.vibrate = target
                else:
                    ch.vibextent = target
            elif op == 0xCC:
                ch.value = _signed(self.u8(ch))
            elif op == 0xC8:
                ch.value = _signed((ch.value - self.u8(ch)) & 255)
            elif op == 0xC9:
                ch.value = _signed(ch.value & self.u8(ch))
            elif op == 0xCB:
                ch.value = _signed(self.data[self.u16(ch) + ch.value])
            elif op == 0xC7:
                add, address = self.u8(ch), self.u16(ch)
                self.data[address] = (ch.value + add) & 255
            elif 0x00 <= op <= 0x0F:
                l = ch.layers.get(op & 15)
                ch.value = int(not l or not l.active)
            elif 0x60 <= op <= 0x6F:
                pass
            elif 0x70 <= op <= 0x7F:
                ch.io[op & 15] = ch.value
            elif 0x80 <= op <= 0x8F:
                index = op & 15
                ch.value = ch.io.get(index, -1)
                if index < 4:
                    ch.io[index] = -1
            elif op in (0xCA, 0xD0, 0xD1, 0xD2, 0xD6, 0xE3, 0xF2):
                self.u8(ch)
            elif op == 0xF1:
                pass
            elif op == 0xF3:
                ch.active = False
                return
            else:
                raise ValueError(f"Unsupported M64 channel command {op:#x} at {ch.pc-1:#x}")
        raise ValueError('Channel loop without a delay')

    def layer(self, ch, l, tick):
        for _ in range(2000):
            if not l.active or l.wake > tick:
                return
            op = self.u8(l)
            if self.flow(l, op, tick):
                if not l.active and l.note:
                    l.note['end'] = min(l.note['end'], tick)
                continue
            if op < 0xC0:
                kind, key = op >> 6, op & 63
                if ch.large:
                    delay = self.var(l) if kind != 2 else l.last_delay
                    velocity = self.u8(l)
                    rest = self.u8(l) if kind != 1 else 0
                    l.duration = rest
                else:
                    delay = self.var(l) if kind == 0 else (l.default_delay if kind == 1 else l.last_delay)
                    velocity, rest = l.velocity, l.duration
                l.last_delay = delay
                delay = max(1, delay)
                if l.note:
                    l.note['end'] = min(l.note['end'], tick)
                key += self.transpose + ch.transpose + l.transpose
                if not 0 <= key <= 127:
                    l.wake = tick + delay
                    return
                event = {'tick': tick, 'end': tick + delay - (delay * rest // 256),
                         'key': key, 'velocity': velocity, 'bank': ch.bank,
                         'instrument': ch.instrument if l.instrument is None else l.instrument,
                         'volume': self.volume * ch.volume * ch.volscale,
                         'pan': l.pan * (1-ch.panweight) + ch.pan * ch.panweight,
                         'panweight': ch.panweight, 'channel_pan': ch.pan,
                         'freq': ch.freq, 'envelope': ch.envelope, 'release': ch.release,
                         'reverb': ch.reverb, 'vibrate': ch.vibrate, 'vibextent': ch.vibextent,
                         'portamento': l.portamento, 'delay': delay, 'channel': ch}
                # Continuous layers update an existing sample instead of restarting it.
                if self.sfx and l.continuous and l.note and l.note['end'] == tick:
                    event['continuous_from'] = self.events.index(l.note)
                self.events.append(event)
                l.note = event
                if l.portamento and l.portamento[0] & 127 == 5:
                    l.portamento = (l.portamento[0], key, l.portamento[2])
                l.wake = tick + delay
                return
            if op == 0xC0:
                if l.note:
                    l.note['end'] = min(l.note['end'], tick)
                l.wake = tick + max(1, self.var(l))
                return
            if op == 0xC1:
                l.velocity = self.u8(l)
            elif op == 0xC2:
                l.transpose = _signed(self.u8(l))
            elif op == 0xC3:
                l.default_delay = self.var(l)
            elif op in (0xC4, 0xC5):
                l.continuous = op == 0xC4
            elif op == 0xC6:
                l.instrument = self.u8(l)
            elif op == 0xC7:
                mode, target = self.u8(l), self.u8(l)
                duration = self.u8(l) if mode & 128 else self.var(l)
                l.portamento = (mode, target + self.transpose + ch.transpose + l.transpose, duration)
            elif op == 0xC8:
                l.portamento = None
            elif op == 0xC9:
                l.duration = self.u8(l)
            elif op == 0xCA:
                l.pan = self.u8(l)
            else:
                raise ValueError(f"Unsupported M64 layer command {op:#x} at {l.pc-1:#x}")
        raise ValueError('Layer loop without a delay')

    def run(self, max_ticks=18000, detect_loop=True):
        seconds = 0.0
        self.loop = None
        for tick in range(max_ticks):
            self.times.append(seconds)
            self.sequence(tick)
            if detect_loop and self.jumps and self.jumps[-1][0] == tick:
                dest = self.jumps[-1][1]
                # A loop entry can be reached by falling through, as in the
                # regular title branch. It need not first be a jump target.
                first_visit = self.address_ticks.get(dest)
                if first_visit is not None and first_visit < tick:
                    self.loop = (first_visit, tick)
                    break
            for ch in self.channels.values():
                self.channel(ch, tick)
                for l in list(ch.layers.values()):
                    self.layer(ch, l, tick)
                # Sound controls apply to notes already playing, too.
                state = (ch.volume * ch.volscale * self.volume, ch.freq, ch.pan,
                         ch.vibrate, ch.vibextent)
                if not ch.controls or ch.controls[-1][1] != state:
                    ch.controls.append((tick, state))
            seconds += 60.0 / (self.tempo * 48.0)
            if self.sfx and all(not ch.active and all(not l.active for l in ch.layers.values())
                                for ch in self.channels.values()):
                self.times.append(seconds)
                break
        else:
            self.times.append(seconds)
        for e in self.events:
            e['end'] = min(e['end'], len(self.times)-1)
        return self


def _sequence_data(rom, index):
    count = struct.unpack_from('>H', rom, ROM_SEQUENCES+2)[0]
    if not 0 <= index < count:
        raise ValueError('ROM sequence out of bounds')
    offset, size = struct.unpack_from('>II', rom, ROM_SEQUENCES+4+index*8)
    return rom[ROM_SEQUENCES+offset:ROM_SEQUENCES+offset+size]


def _sfx_address(data, sound_id):
    """Read the sound player's channel dispatch table directly from ROM."""
    channel, sound = (sound_id >> 28) & 15, (sound_id >> 16) & 255
    # The sound player header contains only fixed-size setup commands.
    pos = 0
    starts = {}
    while pos < 64:
        cmd = data[pos]; pos += 1
        if 0x90 <= cmd <= 0x9F:
            starts[cmd & 15] = struct.unpack_from('>H', data, pos)[0]; pos += 2
        elif cmd in (0xD7, 0xD6): pos += 2
        elif cmd in (0xD3, 0xD5, 0xDB, 0xDD): pos += 1
        elif cmd in (0xFD, 0xFB): break
        else: raise ValueError(f'Unknown sound player header {cmd:#x}')
    pos = starts[channel]
    # chan_setdyntable precedes the channel's main loop.
    for _ in range(32):
        cmd = data[pos]; pos += 1
        if cmd == 0xC2:
            table = struct.unpack_from('>H', data, pos)[0]
            return struct.unpack_from('>H', data, table + sound*2)[0]
        if cmd in (0xC4,) or 0x60 <= cmd <= 0x7F: continue
        if cmd in (0xC1, 0xCC, 0xD0, 0xCA, 0xDC): pos += 1
        else: raise ValueError(f'Sound effect table not found {cmd:#x}')
    raise ValueError('Sound effect table not found')


def _instrument_sound(instrument, key):
    if not instrument: return None
    n = 0 if key < instrument['low'] else 2 if key > instrument['high'] else 1
    return instrument['sounds'][n] or next((s for s in instrument['sounds'] if s), None)


def _envelope_points(data, ptr):
    if ptr is None:
        return [(4, 32000), (-1, 0)]
    points = []
    for i in range(64):
        delay, arg = struct.unpack_from('>hh', data, ptr+i*4)
        points.append((delay, arg))
        if delay <= 0: break
    return points


def _envelope_curve(np, points, seconds):
    """US envelope lines are audio-update counts (four updates per video frame)."""
    x, y, current, elapsed, index = [0.0], [0.0], 0.0, 0.0, 0
    for _ in range(1024):
        delay, target = points[index]
        if delay == -1:  # hang
            break
        if delay == -2:  # goto
            index = target
            if index >= len(points): break
            continue
        if delay <= 0: break
        elapsed += delay / 240.0
        current = max(0, target) / 32767.0
        x.append(elapsed); y.append(current)
        index += 1
        if elapsed > seconds or index >= len(points): break
    x.append(max(elapsed+0.001, seconds)); y.append(current)
    return x, y


def _render_events(rom, player, start_seconds=0.0, end_seconds=None, filter_event=None):
    """PCM synthesis with real loop ranges, score timing, envelopes and panoramas.

    VADPCM samples are decoded exactly. Resampling, vibrato, portamento and
    reverb here are a local PCM approximation, not the N64 RSP bit-exact mixer.
    """
    import io
    try:
        import numpy as np
    except ImportError as error:
        raise ValueError("NumPy is missing. Install dependencies with python3 -m pip install -r requirements.txt") from error
    rate = 32000
    end_seconds = end_seconds if end_seconds is not None else player.times[-1]+0.5
    frames = max(1, round((end_seconds-start_seconds)*rate))
    mix = np.zeros((frames, 2), dtype=np.float64)
    wet = np.zeros_like(mix)
    banks, samples = {}, {}
    events_used = 0
    for e in player.events:
        if filter_event and not filter_event(e): continue
        begin = player.times[e['tick']]
        gate = max(0, player.times[e['end']]-begin)
        if gate <= 0 or begin >= end_seconds: continue
        bank = e['bank']
        if bank not in banks: banks[bank] = _bank_data(rom, bank)
        ctl, tbl, instruments, drums = banks[bank]
        inst = e['instrument']
        synthetic = inst >= 128
        if synthetic:
            # US engine wave IDs 128..131 select its 64-sample PCM tables.
            # Read the tables from the local ROM, rather than bundling samples.
            offsets = (0xEED50, 0xEECD0, 0xEEBD0, 0xEEC50)
            if inst - 128 >= len(offsets):
                raise ValueError(f'Unsupported US synthetic wave {inst}')
            ptr, tuning = offsets[inst - 128], 1.0465
            sound = (ptr, tuning)
            envelope_ptr, release = None, 16
            pan, pitch = e['pan'], 2.0 ** ((e['key']-39)/12.0)
        elif inst == 127:
            drum = drums[min(e['key'], len(drums)-1)] if drums else None
            if not drum: continue
            sound = drum['sound']
            envelope_ptr, release = drum['envelope'], drum['release']
            pan = drum['pan']*(1-e['panweight']) + e['channel_pan']*e['panweight']
            pitch = 1.0
        else:
            instrument = instruments[inst] if inst < len(instruments) else None
            if not instrument: raise ValueError(f'Unsupported instrument {inst} from bank {bank}')
            key = max(e['key'], e['portamento'][1]) if e['portamento'] else e['key']
            sound = _instrument_sound(instrument, key)
            envelope_ptr, release = instrument['envelope'], instrument['release']
            pan, pitch = e['pan'], 2.0 ** ((e['key']-39)/12.0)
        if not sound: continue
        ptr, tuning = sound
        cache_key = (bank, ptr)
        if cache_key not in samples:
            if synthetic:
                pcm = np.frombuffer(rom[ptr:ptr + 128], dtype='>i2').astype(np.float64)
                loop = (0, 64, 1)
            else:
                pcm = np.array(_decode_sample(ctl, tbl, ptr), dtype=np.float64)
                loop_ptr = struct.unpack_from('>I', ctl, ptr+8)[0]
                loop = struct.unpack_from('>III', ctl, loop_ptr) if loop_ptr else (0, len(pcm), 0)
            samples[cache_key] = (pcm, loop)
        pcm, (loop_start, loop_end, loop_count) = samples[cache_key]
        if len(pcm) == 0: continue
        release = e['release'] if e['release'] is not None else release
        # US note_decay subtracts releaseRate*24 per audio update.
        tail = min(0.6, 32767.0 / (max(1, release)*24*240))
        duration = gate + tail
        if not loop_count and not e.get('pitch_segments'): duration = min(duration, len(pcm)/(max(.001,pitch*tuning)*rate)+tail)
        first = round((begin-start_seconds)*rate)
        n = min(round(duration*rate), frames-first)
        if n <= 0 or first+n <= 0: continue
        t = np.arange(n, dtype=np.float64) / rate
        speeds = np.full(n, tuning*pitch, dtype=np.float64)
        # A continuous layer keeps its sample cursor and ADSR while changing pitch.
        segments = e.get('pitch_segments', [e])
        for segment in segments:
            segment_start = player.times[segment['tick']] - begin
            segment_end = player.times[segment['end']] - begin
            selected = (t >= segment_start) & (t < segment_end if segment is not segments[-1] else True)
            local_time = np.maximum(0.0, t - segment_start)
            segment_pitch = 2.0 ** ((segment['key']-39)/12.0)
            segment_speeds = np.full(n, tuning*segment_pitch, dtype=np.float64)
            if segment['portamento']:
                mode, target, amount = segment['portamento']
                target_pitch = 2.0 ** ((target-39)/12.0)
                a, b = (target_pitch, segment_pitch) if mode & 127 in (1,3,5) else (segment_pitch,target_pitch)
                sweep = (segment_end-segment_start)*amount/256 if mode & 128 else amount/240.0
                fraction = np.minimum(1.0, local_time/max(1/rate,sweep))
                factor = 2.0**fraction-1.0
                segment_speeds = tuning*(a+(b-a)*factor)
            speeds[selected] = segment_speeds[selected]
        states = [p for p in e['channel'].controls if len(p)==2 and isinstance(p[1], tuple)]
        if states:
            ticks = np.array([player.times[min(p[0],len(player.times)-1)]-begin for p in states])
            indexes = np.maximum(0, np.searchsorted(ticks, t, side='right')-1)
            freq = np.array([p[1][1] for p in states])[indexes]
            vol = np.array([p[1][0] for p in states])[indexes]
            vib_rate = np.array([p[1][3] for p in states])[indexes]
            vib_extent = np.array([p[1][4] for p in states])[indexes]
            speeds *= freq
        else:
            speeds *= e['freq']; vol = e['volume']
            vib_rate, vib_extent = e['vibrate'], e['vibextent']
        # US: rate command *32, 64 curve positions each 1024 phase units;
        # extent command *8 /4096 scales the octave-bend lookup's deviation.
        updates = np.arange(max(1, math.ceil(duration*240))) / 240.0
        update_indexes = np.minimum(n-1, (updates*rate).astype(int))
        rates = np.broadcast_to(vib_rate,n)[update_indexes]
        phase = np.cumsum(rates*32).astype(np.int64)
        curve_index = (phase >> 10) & 63
        quarter = curve_index & 48
        distance = np.where(quarter == 16,31-curve_index,
                            np.where(quarter == 48,63-curve_index,curve_index & 15))
        curve = distance*8*np.where(curve_index < 32,1,-1)
        curve = curve[np.minimum(len(curve)-1,(t*240).astype(int))]
        speeds *= 1.0+(vib_extent/512.0)*(2.0**(curve/127.0)-1.0)
        positions = np.cumsum(speeds) - speeds[0]
        valid = positions < len(pcm)-1
        if loop_count and loop_end > loop_start:
            positions = np.where(positions >= loop_end,
                                 loop_start + (positions-loop_start) % (loop_end-loop_start),positions)
            valid = np.ones(n, dtype=bool)
        positions = np.clip(positions, 0, len(pcm)-1)
        values = np.interp(positions, np.arange(len(pcm)), pcm) / 32768.0
        values *= valid
        points = _envelope_points(player.data, e['envelope']) if e['envelope'] is not None else _envelope_points(ctl,envelope_ptr)
        ex, ey = _envelope_curve(np, points, duration)
        env = np.interp(np.minimum(t,gate),ex,ey)
        env *= np.maximum(0.0,1.0-np.maximum(0.0,t-gate)/max(tail,1/rate))
        values *= env * (e['velocity']/127.0)**2 * vol * 0.55
        pg = np.array([math.cos(max(0,min(128,pan))/128*math.pi/2),
                       math.sin(max(0,min(128,pan))/128*math.pi/2)])
        contribution = values[:,None]*pg
        skip = max(0,-first); target = max(0,first)
        mix[target:target+n-skip] += contribution[skip:]
        wet[target:target+n-skip] += contribution[skip:]*(e['reverb']/127.0)
        events_used += 1
    # Explicitly approximate the original reverb; use stereo cross feedback,
    # never claim the final PCM is an exact recording of the console.
    for seconds, gain in ((.061,.30),(.089,.24),(.139,.16),(.211,.09),(.307,.05)):
        delay = round(seconds*rate)
        if delay < frames: mix[delay:] += wet[:-delay,::-1]*gain
    peak = float(np.max(np.abs(mix)))
    scale = min(1.0, .94/peak) if peak else 1.0
    pcm = np.rint(np.clip(mix*scale,-1,1)*32767).astype('<i2')
    out = io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm.tobytes())
    return out.getvalue(), {'sample_rate':rate,'output_channels':2,'events':events_used,
                           'duration_seconds':frames/rate,'rom_exact_sample':False,
                           'mixer':'ROM M64 interpreted; PCM mixer approximate',
                           'peak':peak,'limiter_scale':scale}


def _pcm_to_wav(pcm, sample_rate):
    import io
    out = io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sample_rate)
        if __import__('sys').byteorder != 'little':
            pcm = array('h',pcm); pcm.byteswap()
        w.writeframes(pcm.tobytes())
    return out.getvalue()


def _render_one_shot(rom, name):
    spec = TITLE_ONE_SHOTS[name]
    ctl, tbl, instruments, _drums = _bank_data(rom,spec['bank'])
    sound = _instrument_sound(instruments[spec['instrument']],39)
    pcm = _decode_sample(ctl,tbl,sound[0])
    return _pcm_to_wav(pcm,spec['sample_rate']), {
        'sound_id':spec['sound_id'],'sound_bank':spec['bank'],'instrument':spec['instrument'],
        'sample_pointer':sound[0],'sample_tuning':sound[1],'source_frames':len(pcm),
        'sample_rate':spec['sample_rate'],'output_channels':1,
        'duration_seconds':len(pcm)/spec['sample_rate'],'rom_exact_sample':True}


def build_title_music_assets(rom, game_over=False):
    """Select the normal title's ROM branch unless Game Over is requested."""
    variation = 0x80 if game_over else 0
    seq = _Sequence(_sequence_data(rom,2),0x11,variation=variation).run()
    if not seq.loop: raise ValueError('Title music loop not found')
    intro_end, loop_end = (seq.times[t] for t in seq.loop)
    assets = {}
    assets['title_music_intro'] = _render_events(rom,seq,0,intro_end)
    # Include preceding releases crossing the seam at the loop's beginning.
    assets['title_music_loop'] = _render_events(rom,seq,intro_end,loop_end)
    for key in ('title_music_intro','title_music_loop'):
        assets[key][1].update(sequence_id=2,sequence_request_id=2|variation,
                             sequence_variation=variation,
                             menu_state='game_over' if game_over else 'normal_title',
                             sound_bank=0x11,loop_ticks=seq.loop)
    return assets


def build_audio_assets(rom):
    """Build locally from ROM; supplied reference WAVs are never build inputs."""
    assets = build_title_music_assets(rom)
    sound_data = _sequence_data(rom,0)
    effects = {'logo_sound':0x70140080,'coin':0x38118080,
               'hand_appear':0x700A0080,'hand_disappear':0x700B0080,
               'face_pinch':0x70080080,'face_release':0x70090080,'start_star':0x701EFF80,
               'camera_zoom_in':0x70060080,'camera_zoom_out':0x70070080}
    for key, sound_id in effects.items():
        address = _sfx_address(sound_data,sound_id)
        player = _Sequence(sound_data,0,sfx_address=address).run(max_ticks=1200,detect_loop=False)
        assets[key] = _render_events(rom,player)
        assets[key][1].update(sound_id=f'{sound_id:#010x}',sequence_address=address)
        if key == 'logo_sound':
            assets['logo_coin'] = _render_events(rom,player,filter_event=lambda e:e['bank']==9)
    assets['logo_voice'] = _render_one_shot(rom,'logo_voice')
    assets['hello_voice'] = _render_one_shot(rom,'hello_voice')
    return assets
