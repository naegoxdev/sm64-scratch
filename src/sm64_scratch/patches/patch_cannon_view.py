"""Keep the first-act spawn cannon deployed and expose the native pits (V25)."""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition


def apply_cannon_view(project):
    meta = project.setdefault('extensionData', {})
    if meta.get('cannon_view_v25'):
        return meta['cannon_view_v25']
    stage = next(t for t in project['targets'] if t['isStage'])
    target = next(t for t in project['targets'] if t['name'] == 'World + Assets')
    g = Graph(target, stage)
    root = _definition(g, 'WORLD | sync reusable BOB cannons')
    todo, seen = [g.blocks[root]['next']], set()
    while todo:
        key = todo.pop()
        if not key or key in seen:
            continue
        seen.add(key)
        block = g.blocks[key]
        todo.append(block.get('next'))
        for value in block.get('inputs', {}).values():
            if isinstance(value[1], str) and value[1] in g.blocks:
                todo.append(value[1])

    changed = 0
    for key in list(seen):
        block = g.blocks[key]
        if (block['opcode'] == 'data_setvariableto'
                and block['fields']['VARIABLE'][0] == 'BOB.Cannon.VisualY'):
            original_value = block['inputs']['VALUE'][1]
            index = g.var('BOB.Cannon.VisualIndex', local=True)
            deployed = g.set(g.var('BOB.Cannon.VisualY', local=True),
                             g.item('BOB.Cannon.Y', index))
            ordinary = g.set(g.var('BOB.Cannon.VisualY', local=True), original_value)
            wrapper = g.control('control_if_else', g.op('equals', index, 1),
                                [deployed], [ordinary])
            replacement = g.blocks.pop(wrapper)
            replacement.update(parent=block['parent'], next=block['next'])
            g.blocks[key] = replacement
            for node in g.blocks.values():
                if node.get('parent') == wrapper:
                    node['parent'] = key
            changed += 1
    if changed != 2:
        raise ValueError('Unrecognized reusable cannon vertical-placement graph')

    # All six original terrain positions already contain native pits. The
    # authored black planes obscured the retracted meshes down in those pits.
    # Retain their allocated records for compatibility, but never draw them.
    marker = next(key for key in seen if
        g.blocks[key]['opcode'] == 'data_setvariableto'
        and g.blocks[key].get('fields', {}).get('VARIABLE', [''])[0] == 'BOB.Cannon.VisualSceneIndex'
        and 'BOB.Cannon.OpeningIndex' in str(g.blocks[
            g.blocks[key]['inputs']['VALUE'][1]]))
    write = g.blocks[g.blocks[marker]['next']]
    if write['opcode'] != 'data_replaceitemoflist':
        raise ValueError('Unrecognized cannon opening visibility write')
    write['inputs']['ITEM'] = [1, [10, 'hidden']]

    meta['cannon_view_v25'] = dict(version=1,
        reticle='US segment 2 Vtx at 0x11D90, not the source label 0xEF30; four bounded 8x16 triangles',
        spawn='First-act water-bomb cannon remains deployed at its original ROM Y, with player-use adaptation',
        other_cannons='Five standard cannons retain native retraction, rise and reuse',
        openings='Native terrain pits remain visible; authored black marker planes are hidden',
        transitions='V24 live-render rollback preserved', runtime_verified=False)
    return meta['cannon_view_v25']
