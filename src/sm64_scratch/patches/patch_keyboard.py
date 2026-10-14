"""Use full analog-stick deflection for keyboard movement without Shift."""
from copy import deepcopy


def apply_keyboard_running(project):
    mario = next(t for t in project['targets'] if t['name'] == 'Mario')
    blocks = mario['blocks']
    prototype = next(b for b in blocks.values()
                     if b.get('mutation', {}).get('proccode') ==
                     'SM64 INPUT | update joystick inputs')
    definition = blocks[prototype['parent']]
    chain = []
    node = definition['next']
    while node:
        chain.append(node)
        node = blocks[node].get('next')
    for node in chain:
        branch = blocks[node]
        if branch['opcode'] != 'control_if_else':
            continue
        condition_id = branch['inputs']['CONDITION'][1]
        condition = blocks.get(condition_id, {})
        if condition.get('opcode') != 'sensing_keypressed':
            continue
        menu_id = condition['inputs']['KEY_OPTION'][1]
        menu = blocks.get(menu_id, {})
        if menu.get('fields', {}).get('KEY_OPTION', [''])[0] != 'shift':
            continue
        run_id = branch['inputs']['SUBSTACK'][1]
        walk_id = branch['inputs']['SUBSTACK2'][1]
        run = blocks[run_id]
        walk = blocks[walk_id]
        if (run['opcode'] != 'data_setvariableto' or run.get('next') or
                walk['opcode'] != 'data_setvariableto' or walk.get('next') or
                run['fields'] != walk['fields'] or
                float(run['inputs']['VALUE'][1][1]) != 64):
            raise ValueError('Review changed keyboard speed branch before migration.')
        replacement = deepcopy(run)
        replacement.update(parent=branch['parent'], next=branch['next'])
        blocks[node] = replacement
        for obsolete in (condition_id, menu_id, run_id, walk_id):
            del blocks[obsolete]
        break
    if not any(blocks[k].get('opcode') == 'data_setvariableto' and
               blocks[k].get('fields', {}).get('VARIABLE', [''])[0] == 'Math.Temp' and
               blocks[k].get('inputs', {}).get('VALUE') == [1, [4, '64']]
               for k in chain):
        raise ValueError('Missing full-deflection keyboard assignment; review input logic.')
    result = dict(version=1, keyboard_stick_magnitude=64,
                  policy='WASD runs by default; Shift is not required',
                  acceleration='Existing movement acceleration is retained')
    project.setdefault('extensionData', {})['keyboard_running'] = result
    return result
