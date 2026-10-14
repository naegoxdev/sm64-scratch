"""Run gameplay modules in their existing order without five redraw barriers.

Scratch may stop scheduling another pass after any visible sprite requests a
redraw. A broadcast-and-wait between each module then turns one game frame into
several display ticks. The private phase chain executes the same module entry
bodies and yields once at the final rendering phase. Public module
broadcasts remain available for loading, title rendering and scene setup.
"""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival import _clone_chain

PHASES = (
    ('Mario', 'MODULE MARIO UPDATE', 'physics'),
    ('Mario', 'MODULE CAMERA UPDATE', 'camera'),
    ('World + Assets', 'MODULE WORLD SYNC', 'scene'),
    ('World + Assets', 'MODULE ASSETS UPDATE', 'assets'),
    ('Render', 'MODULE RENDER FRAME', 'render'),
)


def _require_synchronous_module(g, head, label):
    definitions = {
        g.blocks[b['inputs']['custom_block'][1]]['mutation']['proccode']: k
        for k, b in g.blocks.items() if b['opcode'] == 'procedures_definition'
    }
    visited = set()
    def visit(identity):
        if not identity or identity in visited:
            return
        visited.add(identity)
        block = g.blocks[identity]
        if block['opcode'] in ('control_wait', 'control_wait_until',
                               'event_broadcastandwait', 'sound_playuntildone',
                               'sound_setvolumeto', 'sound_changevolumeby'):
            raise ValueError('Frame queue requires a synchronous module: ' + label +
                             ' contains ' + block['opcode'])
        if block['opcode'] == 'procedures_call':
            code = block['mutation']['proccode']
            definition = definitions.get(code)
            if definition is None:
                raise ValueError('Missing frame procedure: ' + code)
            proto = g.blocks[g.blocks[definition]['inputs']['custom_block'][1]]
            if proto['mutation'].get('warp') != 'true':
                raise ValueError('Frame queue requires a warp procedure: ' + code)
            visit(g.blocks[definition].get('next'))
        visit(block.get('next'))
        for inp in block.get('inputs', {}).values():
            for child in inp[1:]:
                if isinstance(child, str) and child in g.blocks:
                    visit(child)
    visit(head)
    return len(visited)


def apply_frame_pipeline(project):
    stage = next(t for t in project['targets'] if t['isStage'])
    targets = {t['name']: t for t in project['targets']}
    g = Graph(targets['Main Loop'], stage)
    done = g.var('Frame.RenderScheduled', 0)
    events = [('porter_frame_' + key, 'ENGINE FRAME ' + key.upper()) for _, _, key in PHASES]
    for identity, label in events:
        stage['broadcasts'][identity] = label
    stage['broadcasts']['porter_frame_rendered'] = 'ENGINE FRAME RENDERED'
    audited = {}
    for i, (name, original, key) in enumerate(PHASES):
        g = Graph(targets[name], stage)
        code = 'FRAME | update ' + key
        hat = next(b for b in g.blocks.values() if b['opcode'] == 'event_whenbroadcastreceived'
                   and b['fields']['BROADCAST_OPTION'][0] == original)
        if not hat.get('next'):
            raise ValueError('Missing module entry: ' + original)
        audited[original] = _require_synchronous_module(g, hat['next'], original)
        if any(b['opcode'] == 'procedures_prototype' and b['mutation']['proccode'] == code
               for b in g.blocks.values()):
            continue
        head = _clone_chain(g, hat['next'])
        tail = head
        while g.blocks[tail].get('next'):
            tail = g.blocks[tail]['next']
        if i == len(PHASES) - 1:
            final = g.node('event_broadcast')
            g.blocks[final]['inputs'] = {'BROADCAST_INPUT': [1, [11, 'ENGINE FRAME RENDERED', 'porter_frame_rendered']]}
        else:
            identity, label = events[i + 1]
            final = g.node('event_broadcast')
            g.blocks[final]['inputs'] = {'BROADCAST_INPUT': [1, [11, label, identity]]}
        # Enqueue first: native 'stop this script' exits must still advance.
        # Every called module is synchronous and warp; queued workers run
        # after this worker has returned, in the same scheduler pass.
        g.blocks[final]['next'] = head
        g.blocks[head]['parent'] = final
        head = final
        g.procedure(code, [])
        definition = next(k for k, b in g.blocks.items() if b['opcode'] == 'procedures_definition'
                          and g.blocks[b['inputs']['custom_block'][1]]['mutation']['proccode'] == code)
        g.blocks[definition]['next'] = head
        g.blocks[head]['parent'] = definition
        identity, label = events[i]
        g.hat('event_whenbroadcastreceived', [g.call(code)], [label, identity])
    g = Graph(targets['Main Loop'], stage)
    if not any(b['opcode'] == 'event_whenbroadcastreceived' and b.get('fields', {}).get('BROADCAST_OPTION', [None])[0] == 'ENGINE FRAME RENDERED' for b in g.blocks.values()):
        g.hat('event_whenbroadcastreceived', [g.set(done, 1)], ['ENGINE FRAME RENDERED', 'porter_frame_rendered'])
    if 'porter_frame_queue_reset' not in g.blocks:
        identity, label = events[0]
        start = g.node('event_broadcast')
        g.blocks[start]['inputs'] = {'BROADCAST_INPUT': [1, [11, label, identity]]}
        # The ordinary forever yields once per display tick. No busy wait lives
        # inside warp mode, and a frame is queued only after the previous rendering pass has returned.
        g.procedure('ENGINE | queue frame', [g.control('control_if',
            g.op('and', g.op('equals', g.var('all.run'), 1), g.op('equals', done, 1)),
            [g.call('TIME | update'), g.set(done, 0), start])])
        g.blocks['/V']['mutation']['proccode'] = 'ENGINE | queue frame'
        g.blocks['/V']['mutation']['warp'] = 'true'
        parent = g.blocks['CH']['parent']
        reset = g.set(done, 1)
        g.blocks['porter_frame_queue_reset'] = g.blocks.pop(reset)
        g.insert_after(parent, ['porter_frame_queue_reset'])
    result = dict(version=1, order=[label for _, label, _ in PHASES],
                  completion='Ordinary frame loop yields once per display tick; synchronous workers are ordered in that pass',
                  physics='Existing elapsed-time accumulator and 30 Hz fixed tick retained',
                  public_module_entries_preserved=True, synchronous_module_blocks=audited)
    project.setdefault('extensionData', {})['completed_frame_pipeline'] = result
    return result
