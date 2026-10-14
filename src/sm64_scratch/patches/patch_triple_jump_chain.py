"""Prevent the authored landing controller from chaining triple jumps forever.

SM64 clears A during ACT_TRIPLE_JUMP_LAND and the stationary triple-jump landing
restarts with ACT_JUMP, not another ACT_TRIPLE_JUMP. The existing Scratch
controller treated triple-jump landings like double-jump landings, leaving the
jump chain at 2 for a few frames and allowing immediate repeated triple flips.
"""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition, _call


def apply_triple_jump_chain_fix(project):
    meta = project.setdefault('extensionData', {})
    if meta.get('triple_jump_chain_v30'):
        return meta['triple_jump_chain_v30']

    stage = next(t for t in project['targets'] if t.get('isStage'))
    mario = next(t for t in project['targets'] if t['name'] == 'Mario')
    g = Graph(mario, stage)
    _definition(g, 'SM64 ACT | landing')  # fail early if the expected controller is absent

    # Stable authored landing block: A-pressed branch currently calls the
    # generic jump-chain helper for every landing type.
    a_branch = g.blocks.get('dj')
    if not a_branch or a_branch.get('opcode') != 'control_if_else':
        raise ValueError('Triple-jump chain patch: landing A branch not found')
    old_call = a_branch['inputs']['SUBSTACK'][1]
    if not old_call or g.blocks[old_call].get('mutation', {}).get('proccode') != 'SM64 STATE | set jump from landing':
        raise ValueError('Triple-jump chain patch: generic jump call not found')

    prev_is_triple = g.op('equals', g.var('SM64.PrevAction'), 'ACT_TRIPLE_JUMP')
    stopped = g.op('equals', g.var('SM64.Action'), 'ACT_TRIPLE_JUMP_LAND_STOP')
    restart_normal = _call(g, 'SM64 STATE | set action %s arg %s', ['ACT_JUMP', 0])
    stopped_if = g.control('control_if', stopped, [restart_normal])
    replacement = g.control('control_if_else', prev_is_triple, [stopped_if], [old_call])
    a_branch['inputs']['SUBSTACK'] = [2, replacement]
    g.blocks[replacement]['parent'] = 'dj'
    g.blocks[old_call]['parent'] = replacement

    # The landing initialization currently leaves JumpChain=2 and a five-frame
    # double-jump timer for both double and triple landings. Clear both only
    # when this landing actually came from ACT_TRIPLE_JUMP. This also prevents
    # a quick transition to WALKING from preserving the stale triple chain.
    timer_set = g.blocks.get('ars')
    if not timer_set or timer_set.get('fields', {}).get('VARIABLE', [''])[0] != 'SM64.DoubleJumpTimer':
        raise ValueError('Triple-jump chain patch: landing timer anchor not found')
    reset = g.control('control_if', g.op('equals', g.var('SM64.PrevAction'), 'ACT_TRIPLE_JUMP'), [
        g.set(g.var('SM64.JumpChain'), 0),
        g.set(g.var('SM64.DoubleJumpTimer'), 0),
    ])
    g.insert_after('ars', [reset])

    result = dict(version=1,
                  triple_jump_moving_landing='A press ignored for the landing edge',
                  triple_jump_stationary_landing='A restarts ACT_JUMP',
                  jump_chain_after_triple=0,
                  note='Backflip/side-flip authored behavior is otherwise unchanged',
                  runtime_verified=False)
    meta['triple_jump_chain_v30'] = result
    return result
