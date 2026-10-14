"""Reconstruct the US pipe-to-dialog camera spline from the owned ROM."""
import hashlib
import json
import struct
from pathlib import Path

from sm64_scratch.paths import METADATA_DIR


def build_pipe_camera_payload(rom):
    recipe = json.loads((METADATA_DIR / 'pipe_camera.json').read_text())
    if hashlib.sha1(rom).hexdigest() != recipe['rom_sha1']:
        raise ValueError('Unsupported pipe camera ROM')
    curves = {}
    for kind in ('position', 'focus'):
        offset = recipe[kind + '_rom_offset']
        data = rom[offset:offset + recipe['point_count'] * recipe['point_stride']]
        if hashlib.sha256(data).hexdigest() != recipe[kind + '_sha256']:
            raise ValueError('Pipe camera spline source differs: ' + kind)
        points = list(struct.iter_unpack('>bBhhh', data))
        if points[-1][0] != -1 or any(point[0] == -1 for point in points[:-1]):
            raise ValueError('Invalid camera spline terminator')
        curves[kind] = points
    segment, progress = 0, recipe['initial_progress']
    payload = {'Intro.' + kind + '.' + axis: []
               for kind in ('Camera', 'Focus') for axis in 'XYZ'}
    for _ in range(recipe['sample_count']):
        u = min(1, progress)
        weights = ((1-u)**3 / 6, u**3 / 2-u*u+2/3,
                   -u**3 / 2+u*u / 2+u / 2+1/6, u**3 / 6)
        # Position rows have zero speed. Focus advances the shared clock once.
        for label, kind in (('Camera', 'position'), ('Focus', 'focus')):
            points = curves[kind]
            if segment + 3 >= len(points) or any(points[segment+i][0] == -1 for i in range(3)):
                raise ValueError('Pipe camera samples exceed the native spline')
            for axis, offset, channel in zip('XYZ', recipe['world_translation'], range(3)):
                value = sum(weights[i] * points[segment+i][channel+2] for i in range(4))
                payload['Intro.' + label + '.' + axis].append(
                    round(value / recipe['coordinate_divisor'] + offset, recipe['round_decimals']))
        speeds = [curves['focus'][segment+i][1] for i in (1, 2)]
        start, end = (1/speed if speed else 0 for speed in speeds)
        progress += start + (end-start) * progress
        if progress >= 1:
            segment += 1
            progress -= 1
    for name, values in payload.items():
        digest = hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()
        if digest != recipe['destination_hashes'][name]:
            raise ValueError('Pipe camera reference calibration differs: ' + name)
    return payload, {'camera_samples': recipe['sample_count'],
                     'camera_curve': 'ROM US pipe-to-dialog cubic spline',
                     'camera_reference_rounding': recipe['round_decimals'],
                     'camera_destination_hashes_verified': True}
