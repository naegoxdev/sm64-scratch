from sm64_scratch.patches.patch_animation import Graph


def _unlink_statement(blocks, key):
    block = blocks[key]
    parent = block.get('parent')
    following = block.get('next')
    if parent and parent in blocks:
        p = blocks[parent]
        if p.get('next') == key:
            p['next'] = following
        else:
            found = False
            for name, inp in p.get('inputs', {}).items():
                if isinstance(inp, list) and len(inp) > 1 and inp[1] == key:
                    inp[1] = following
                    found = True
                    break
            if not found:
                raise ValueError(f'Cannot unlink {key}: parent {parent} does not reference it')
    if following and following in blocks:
        blocks[following]['parent'] = parent
    del blocks[key]


def apply_bob_entry_stall_fix(project):
    meta = project.setdefault('extensionData', {})
    if meta.get('bob_entry_stall_fix', {}).get('version') == 1:
        return meta['bob_entry_stall_fix']
    stage = next(t for t in project['targets'] if t.get('isStage'))
    targets = {t['name']: t for t in project['targets']}

    # Match the death-wipe scheduling: do not submit the expensive BOB request
    # until a completely opaque painting frame has actually been presented.
    mg = Graph(targets['Mario'], stage)
    closed = mg.var('Painting.ClosedFrameShown', 0)
    if 'ultimate_b_39000013' not in mg.blocks or 'ultimate_b_39000064' not in mg.blocks:
        raise ValueError('Painting entry graph differs from expected build')
    mg.insert_after('ultimate_b_39000013', [mg.set(closed, 0)])

    # Existing branch: Fade=100 -> State=2 -> Level.Request=BOB.
    # New branch: Fade=100 -> if ClosedFrameShown=1 then State=2/request.
    fade100 = 'ultimate_b_39000061'
    state2 = 'ultimate_b_39000062'
    request = 'ultimate_b_39000063'
    guard = mg.control('control_if', mg.op('equals', closed, 1), [state2, request])
    mg.blocks[fade100]['next'] = guard
    mg.blocks[guard]['parent'] = fade100
    mg.blocks['ultimate_b_39000064']['inputs']['SUBSTACK'] = [2, fade100]
    mg.blocks[fade100]['parent'] = 'ultimate_b_39000064'

    # ENGINE FRAME RENDERED is the same acknowledgement point used by the
    # smooth death/Bowser wipe. Set the latch only after the fully opaque white
    # costume has been selected and shown.
    tg = Graph(targets['Transitions'], stage)
    tclosed = tg.var('Painting.ClosedFrameShown', 0)
    if 'v13_anim_Transitions_99' not in tg.blocks:
        raise ValueError('Active painting transition presenter differs from expected build')
    tg.insert_after('v13_anim_Transitions_99', [
        tg.control('control_if', tg.op('equals', tg.var('Painting.Fade'), 100), [tg.set(tclosed, 1)])
    ])
    # Reset on green flag too.
    if 'v13_anim_Transitions_47' in tg.blocks:
        tg.insert_after('v13_anim_Transitions_47', [tg.set(tclosed, 0)])

    # BOB's initialization rebuilt the render queue seven times while the
    # scene was still hidden. None of the creation/edit routines needs that
    # queue before rendering, so collapse them to one final rebuild.
    wg = Graph(targets['World + Assets'], stage)
    redundant = [
        '^v',
        'ultimate_b_49500012',
        'v13_anim_World_+_Assets_1606',
        'v13_anim_World_+_Assets_1817',
        'v13_anim_World_+_Assets_1884',
        'v13_anim_World_+_Assets_2126',
        'v13_anim_World_+_Assets_2400',
    ]
    removed = []
    for key in redundant:
        b = wg.blocks.get(key)
        if b and b.get('opcode') == 'procedures_call' and b.get('mutation', {}).get('proccode') == 'SCENE | rebuild render queue':
            _unlink_statement(wg.blocks, key)
            removed.append(key)
    if 'v13_anim_World_+_Assets_1732' not in wg.blocks:
        raise ValueError('BOB scene tail differs from expected build')
    final_rebuild = wg.call('SCENE | rebuild render queue')
    wg.insert_after('v13_anim_World_+_Assets_1732', [final_rebuild])

    report = {
        'version': 1,
        'painting_close_ack': 'BOB request waits for a fully opaque white frame presented on ENGINE FRAME RENDERED',
        'request_timing': 'Painting.State remains 1 at Fade=100 until Painting.ClosedFrameShown == 1',
        'render_queue_rebuilds_removed': len(removed),
        'render_queue_policy': 'one final rebuild after all BOB scene/object creation',
        'expected_effect': 'no frozen partially-white painting frame; shorter atomic BOB load',
    }
    meta['bob_entry_stall_fix'] = report
    return report
