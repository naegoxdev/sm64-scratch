"""Keep BOB's native 13 signposts, but snap the one unsupported ledge sign to Scratch collision.

The original SM64 BOB macro data contains 13 wooden signposts. The Scratch
reconstruction also creates all 13, so the count is intentional. One native
sign (dialog 50, x=-35.30 z=4.30 in the project's scaled coordinates) sits on
collision that the current reconstructed BOB collision set does not expose at
its native y=14.15. In Scratch that sign visibly floats. Snap only that sign to
the highest reconstructed floor underneath it (y=7.68).
"""
from __future__ import annotations
import json

CREATE = 'SCENE | create object %s type %s model %s at %s %s %s rotation %s %s %s script %s'


def _target(project, name):
    return next(t for t in project['targets'] if t.get('name') == name)


def _literal(inp):
    if not isinstance(inp, list):
        return None
    for item in inp[1:]:
        if isinstance(item, list) and len(item) > 1 and item[0] in (4, 10):
            return item[1]
    return None


def apply_bob_sign_fixes(project):
    ext = project.setdefault('extensionData', {})
    if ext.get('bob_sign_floor_fix_v1'):
        return ext['bob_sign_floor_fix_v1']

    world = _target(project, 'World + Assets')
    blocks = world['blocks']
    changed = []
    sign_count = 0
    for bid, block in blocks.items():
        if block.get('opcode') != 'procedures_call':
            continue
        mut = block.get('mutation', {})
        if mut.get('proccode') != CREATE:
            continue
        ids = json.loads(mut.get('argumentids', '[]'))
        if len(ids) < 6:
            continue
        name = _literal(block.get('inputs', {}).get(ids[0]))
        if isinstance(name, str) and name.startswith('BOB Sign '):
            sign_count += 1
        if name != 'BOB Sign 9':
            continue
        x = _literal(block['inputs'].get(ids[3]))
        y = _literal(block['inputs'].get(ids[4]))
        z = _literal(block['inputs'].get(ids[5]))
        # Guard against silently editing a different future object layout.
        if abs(float(x) - (-35.3)) > 0.001 or abs(float(z) - 4.3) > 0.001:
            raise RuntimeError(f'Unexpected BOB Sign 9 position: {(x, y, z)}')
        block['inputs'][ids[4]] = [1, [4, '7.68']]
        changed.append({'block': bid, 'from_y': float(y), 'to_y': 7.68})

    if sign_count != 13:
        raise RuntimeError(f'Expected native BOB sign count 13, found {sign_count}')
    if len(changed) != 1:
        raise RuntimeError(f'Expected to snap exactly one floating BOB sign, changed {len(changed)}')

    report = {
        'version': 1,
        'native_sign_count_preserved': 13,
        'floating_signs_snapped': changed,
        'reason': 'native sign 9 sits above missing reconstructed collision at y=14.15; Scratch floor is y=7.68',
    }
    ext['bob_sign_floor_fix_v1'] = report
    return report
