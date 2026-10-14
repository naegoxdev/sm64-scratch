"""Use SM64's forward-rollout animation after a dive slide.

The old Scratch branch mapped ACT_FORWARD_ROLLOUT to clip 0xC1 (triple jump).
SM64's act_forward_rollout uses MARIO_ANIM_FORWARD_SPINNING (0x6F / clip 111)
for the actual rollout, so replace only that request.
"""
from __future__ import annotations
import json

REQUEST = 'ANIM | request %s loop %s speed %s'


def _target(project, name):
    return next(t for t in project['targets'] if t.get('name') == name)


def _literal(inp):
    if not isinstance(inp, list):
        return None
    for item in inp[1:]:
        if isinstance(item, list) and len(item) > 1 and item[0] in (4, 10):
            return item[1]
    return None


def _is_rollout_condition(blocks, cid):
    c = blocks.get(cid, {})
    if c.get('opcode') != 'operator_equals':
        return False
    text = json.dumps(c, ensure_ascii=False)
    return 'SM64.Action' in text and 'ACT_FORWARD_ROLLOUT' in text


def apply_dive_rollout_animation(project):
    ext = project.setdefault('extensionData', {})
    if ext.get('dive_rollout_animation_v1'):
        return ext['dive_rollout_animation_v1']

    mario = _target(project, 'Mario')
    blocks = mario['blocks']
    changed = []
    for ifid, block in blocks.items():
        if block.get('opcode') != 'control_if_else':
            continue
        cond = block.get('inputs', {}).get('CONDITION', [None, None])[1]
        if not isinstance(cond, str) or not _is_rollout_condition(blocks, cond):
            continue
        sub = block.get('inputs', {}).get('SUBSTACK', [None, None])[1]
        cur = sub
        seen = set()
        while isinstance(cur, str) and cur in blocks and cur not in seen:
            seen.add(cur)
            b = blocks[cur]
            if b.get('opcode') == 'procedures_call' and b.get('mutation', {}).get('proccode') == REQUEST:
                ids = json.loads(b['mutation'].get('argumentids', '[]'))
                old = _literal(b.get('inputs', {}).get(ids[0])) if ids else None
                b['inputs'][ids[0]] = [1, [10, 'SM64 Forward Spinning (decomp)']]
                changed.append({'block': cur, 'from': old, 'to': 'SM64 Forward Spinning (decomp)'})
                break
            cur = b.get('next')

    # The current stable graph has one explicit ACT_FORWARD_ROLLOUT animation branch.
    if len(changed) != 1:
        raise RuntimeError(f'Expected one rollout animation request, changed {len(changed)}')

    report = {
        'version': 1,
        'action': 'ACT_FORWARD_ROLLOUT',
        'clip': 'MARIO_ANIM_FORWARD_SPINNING',
        'clip_id': 111,
        'changed': changed,
    }
    ext['dive_rollout_animation_v1'] = report
    return report
