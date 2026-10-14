"""Speed up scene-object construction without changing the Scene list format.

The original Scratch helper creates a new object by repeatedly calling
``SCENE | insert object`` for every property. That helper searches the Scene
list for ``<name>.start``/``<name>.end`` on every property, so level startup
becomes quadratic as the scene grows.

This patch only changes ``SCENE | create object``: for a brand-new object it
appends the exact same marker/property/value structure directly. Existing
object editing still uses the original insertion helper.
"""
from __future__ import annotations

CREATE = 'SCENE | create object %s type %s model %s at %s %s %s rotation %s %s %s script %s'
INSERT = 'SCENE | insert object %s data %s value %s'


def _target(project, name):
    return next(t for t in project['targets'] if t.get('name') == name)


def _find_definition(blocks, proccode):
    for did, b in blocks.items():
        if b.get('opcode') != 'procedures_definition':
            continue
        proto = b.get('inputs', {}).get('custom_block', [None, None])[1]
        if isinstance(proto, str) and proto in blocks:
            if blocks[proto].get('mutation', {}).get('proccode') == proccode:
                return did
    raise ValueError('Procedure not found: ' + proccode)


def _reporter_roots(block, blocks):
    roots = []
    for value in block.get('inputs', {}).values():
        if isinstance(value, list):
            for item in value[1:]:
                if isinstance(item, str) and item in blocks:
                    roots.append(item)
    return roots


def _delete_reporter_tree(blocks, root, protected=()):
    protected = set(protected)
    todo = [root]
    seen = set()
    while todo:
        bid = todo.pop()
        if bid in seen or bid in protected or bid not in blocks:
            continue
        seen.add(bid)
        b = blocks[bid]
        for value in b.get('inputs', {}).values():
            if isinstance(value, list):
                for item in value[1:]:
                    if isinstance(item, str) and item in blocks:
                        todo.append(item)
    for bid in seen:
        blocks.pop(bid, None)


def apply_fast_scene_create(project):
    ext = project.setdefault('extensionData', {})
    if ext.get('fast_scene_create_v1'):
        return ext['fast_scene_create_v1']

    world = _target(project, 'World + Assets')
    blocks = world['blocks']
    definition = _find_definition(blocks, CREATE)
    first = blocks[definition].get('next')
    if not first:
        raise ValueError('SCENE | create object has no body')

    # The stable base creates a 3-item shell, adds an end marker, then inserts
    # 19 property/value pairs before it. We keep the existing shell reporters,
    # but append the 19 pairs directly and move the existing end marker last.
    chain = []
    cur = first
    seen = set()
    while cur and cur in blocks and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        cur = blocks[cur].get('next')

    insert_calls = [bid for bid in chain
                    if blocks[bid].get('opcode') == 'procedures_call'
                    and blocks[bid].get('mutation', {}).get('proccode') == INSERT]
    if len(insert_calls) != 19:
        raise ValueError(f'Expected 19 object-property inserts, found {len(insert_calls)}')

    # Identify the end-marker append: it is the only pre-property add-to-Scene
    # statement between the initial object-name append and the first insert.
    first_insert_index = chain.index(insert_calls[0])
    prefix = chain[:first_insert_index]
    scene_adds = [bid for bid in prefix
                  if blocks[bid].get('opcode') == 'data_addtolist'
                  and blocks[bid].get('fields', {}).get('LIST', [None])[0] == 'Scene']
    if len(scene_adds) < 3:
        raise ValueError('Unexpected SCENE | create object prefix')
    end_marker = scene_adds[-1]
    before_end = blocks[end_marker].get('parent')
    if not before_end or blocks[before_end].get('next') != end_marker:
        raise ValueError('Could not detach object end marker')

    # The object's record length becomes 1 start marker + object name +
    # 19*(property,value) + 1 end marker = 41.
    length_block = None
    for bid in prefix:
        b = blocks[bid]
        if b.get('opcode') == 'data_addtolist' and b.get('inputs', {}).get('ITEM') == [1, [10, '3']]:
            length_block = bid
            break
    if length_block is None:
        raise ValueError('Object record-length initializer not found')
    blocks[length_block]['inputs']['ITEM'] = [1, [10, '41']]

    # Detach the end marker from the prefix. The first insert will follow the
    # previous prefix statement directly.
    first_call = insert_calls[0]
    blocks[before_end]['next'] = first_call
    blocks[first_call]['parent'] = before_end

    # Convert each insert call into two direct appends. Preserve the existing
    # call ID for the property-name append so external references remain valid.
    created = []
    for index, bid in enumerate(insert_calls, start=1):
        old = blocks[bid]
        inputs = list(old.get('inputs', {}).values())
        # Procedure calls preserve argument order in mutation.argumentids.
        import json
        argids = json.loads(old.get('mutation', {}).get('argumentids', '[]'))
        if len(argids) != 3:
            raise ValueError('Unexpected insert-object signature at ' + bid)
        object_key = old['inputs'][argids[0]]
        prop_value = old['inputs'][argids[1]]
        data_value = old['inputs'][argids[2]]
        old_next = old.get('next')

        # Delete reporter subtree used only to find <name>.end. The direct
        # append does not need it. Property/data reporters stay referenced.
        for root in _reporter_roots({'inputs': {'X': object_key}}, blocks):
            _delete_reporter_tree(blocks, root)

        old.clear()
        old.update({
            'opcode': 'data_addtolist',
            'next': None,
            'parent': None,
            'inputs': {'ITEM': prop_value},
            'fields': {'LIST': ['Scene', next(v[1] for v in world['lists'].values() if v[0] == 'Scene')] if any(v[0] == 'Scene' for v in world.get('lists', {}).values()) else None},
            'shadow': False,
            'topLevel': False,
        })
        # Scene is normally a Stage list, not local. Copy the field from the
        # known prefix block to avoid relying on ownership.
        old['fields'] = {'LIST': list(blocks[before_end]['fields']['LIST'])}

        new_id = f'fastscene_release_{index:02d}'
        suffix = 1
        while new_id in blocks:
            suffix += 1
            new_id = f'fastscene_release_{index:02d}_{suffix}'
        blocks[new_id] = {
            'opcode': 'data_addtolist',
            'next': old_next,
            'parent': bid,
            'inputs': {'ITEM': data_value},
            'fields': {'LIST': list(blocks[before_end]['fields']['LIST'])},
            'shadow': False,
            'topLevel': False,
        }
        old['next'] = new_id
        created.append(new_id)
        if old_next and old_next in blocks:
            blocks[old_next]['parent'] = new_id

    # Move the existing object end marker after the final property value.
    last_value = created[-1]
    blocks[last_value]['next'] = end_marker
    blocks[end_marker]['parent'] = last_value
    blocks[end_marker]['next'] = None

    # Repair parents along the converted chain from the prefix to the end.
    prev = before_end
    cur = blocks[prev].get('next')
    guard = 0
    while cur and cur in blocks and guard < 100:
        blocks[cur]['parent'] = prev
        prev = cur
        cur = blocks[cur].get('next')
        guard += 1

    report = {
        'version': 1,
        'properties_appended_directly': len(insert_calls),
        'record_items': 41,
        'purpose': 'avoid repeated full Scene scans while constructing new objects',
        'format_compatible': True,
    }
    ext['fast_scene_create_v1'] = report
    return report
