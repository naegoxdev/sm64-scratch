"""Protect scene records when a partial ROM build lacks actor tables.

Empty cached-index items become numeric zero and ambient loops can overwrite
Arena when they add field offsets to that zero. Skip these cached writes.
Scratch's item-number reporter also returns zero for a missing object marker.
The original editor subtracts one and searches forward for a field, which
can then overwrite an unrelated object. No media or game data is added.
"""
import copy
from collections import defaultdict

from sm64_scratch.patches.patch_animation import Graph


def _clone_reporter(graph, key):
    """Clone a reporter tree without sharing its new parent links."""
    block = graph.blocks[key]
    clone = graph.node(block['opcode'], fields=copy.deepcopy(block['fields']))
    for name, value in block['inputs'].items():
        value = copy.deepcopy(value)
        for i in range(1, len(value)):
            if isinstance(value[i], str) and value[i] in graph.blocks:
                value[i] = _clone_reporter(graph, value[i])
                graph.blocks[value[i]]['parent'] = clone
        graph.blocks[clone]['inputs'][name] = value
    return clone


def _guard_cached_scene_writes(graph):
    """An empty actor cache must never become an offset into Arena's record."""
    guarded = 0
    for key, block in list(graph.blocks.items()):
        if (not isinstance(block, dict) or block['opcode'] != 'data_replaceitemoflist'
                or block['fields'].get('LIST', [''])[0] != 'Scene'):
            continue
        index = block['inputs'].get('INDEX', [None, None])[1]
        reporter = graph.blocks.get(index, {}) if isinstance(index, str) else {}
        if reporter.get('opcode') != 'operator_add':
            continue
        cache_key = reporter['inputs'].get('NUM1', [None, None])[1]
        cache = graph.blocks.get(cache_key, {}) if isinstance(cache_key, str) else {}
        if (cache.get('opcode') != 'data_itemoflist'
                or not cache['fields'].get('LIST', [''])[0].endswith('SceneIndex')):
            continue
        parent = graph.blocks[block['parent']]
        # This also survives Scratch/TurboWarp block-ID regeneration on save.
        condition = graph.blocks.get(parent.get('inputs', {}).get('CONDITION', [None, None])[1], {})
        guarded_cache = condition.get('inputs', {}).get('OPERAND1', [None, None])[1]
        guarded_reporter = graph.blocks.get(guarded_cache, {}) if isinstance(guarded_cache, str) else {}
        if (parent['opcode'] == 'control_if'
                and parent.get('inputs', {}).get('SUBSTACK') == [2, key]
                and condition.get('opcode') == 'operator_gt'
                and guarded_reporter.get('opcode') == 'data_itemoflist'
                and guarded_reporter.get('fields') == cache.get('fields')
                and condition['inputs'].get('OPERAND2') == [1, [10, '0']]):
            guarded += 1
            continue
        guard = graph.node('control_if', {'CONDITION': graph.op('gt', _clone_reporter(graph, cache_key), 0)})
        graph.blocks[guard].update(parent=block['parent'], next=block['next'])
        graph.blocks[guard]['inputs']['SUBSTACK'] = [2, key]
        if parent.get('next') == key:
            parent['next'] = guard
        else:
            links = [v for v in parent['inputs'].values() if len(v) > 1 and v[1] == key]
            if len(links) != 1:
                raise ValueError('Unsupported cached scene write parent: ' + key)
            links[0][1] = guard
        if block['next']:
            graph.blocks[block['next']]['parent'] = guard
        block.update(parent=guard, next=None)
        guarded += 1
    return guarded


def _separate_shared_reporters(graph):
    """Give each Scratch input its own reporter tree and correct parent link.

    Some authored graphs reuse one reporter ID in several inputs. Scratch's
    importer follows parent links and can discard those inputs, turning a
    valid actor index into zero. Sharing a value is fine; sharing a block is
    not part of Scratch's tree representation.
    """
    references = defaultdict(list)
    for key, block in list(graph.blocks.items()):
        if not isinstance(block, dict):
            continue
        for name, value in block.get('inputs', {}).items():
            child = value[1] if len(value) > 1 else None
            if isinstance(child, str) and child in graph.blocks:
                references[child].append((key, name))
    separated = 0
    for child, parents in references.items():
        for parent, name in parents[1:]:
            clone = _clone_reporter(graph, child)
            graph.blocks[parent]['inputs'][name][1] = clone
            graph.blocks[clone]['parent'] = parent
            separated += 1
        graph.blocks[child]['parent'] = parents[0][0]
    return separated


def apply_scene_lookup_guards(project):
    stage = next(t for t in project['targets'] if t.get('isStage'))
    patched = []
    cached_writes = {}
    shared_reporters = {}
    procedures = {
        'SCENE | edit object %s data %s value %s operation %s': -1,
        'SCENE | insert object %s data %s value %s': 0,
    }
    for target in project['targets']:
        graph = Graph(target, stage)
        for proto in list(target['blocks'].values()):
            if not isinstance(proto, dict):
                continue  # Scratch may serialize standalone reporters as arrays.
            code = proto.get('mutation', {}).get('proccode')
            if proto['opcode'] != 'procedures_prototype' or code not in procedures:
                continue
            marker = 'phase2_scene_lookup_' + ('edit' if 'edit object' in code else 'insert')
            first = graph.blocks[proto['parent']]['next']
            existing = graph.blocks.get(graph.blocks[first].get('next'), {})
            condition = graph.blocks.get(existing.get('inputs', {}).get('CONDITION', [None, None])[1], {})
            if marker in graph.blocks or (existing.get('opcode') in ('control_if', 'control_if_else')
                                         and condition.get('opcode') == 'operator_gt'):
                patched.append(target['name'] + ': ' + code)
                continue
            lookup = graph.blocks[first]
            if (lookup['opcode'] != 'data_setvariableto'
                    or lookup['fields'].get('VARIABLE', [''])[0] != 'Scene.Object#'):
                raise ValueError('Scene lookup implementation changed: ' + code)
            body = lookup['next']
            condition = graph.op('gt', graph.var('Scene.Object#'), procedures[code])
            guard = graph.node('control_if', {'CONDITION': condition})
            graph.blocks[marker] = graph.blocks.pop(guard)
            graph.blocks[condition]['parent'] = marker
            graph.blocks[marker].update(parent=first, inputs={
                'CONDITION': [2, condition], 'SUBSTACK': [2, body]})
            lookup['next'] = marker
            graph.blocks[body]['parent'] = marker
            # Preserve the editor's explicit missing-result sentinel.
            if 'edit object' in code:
                missing = graph.set(graph.var('data.return'), 'null')
                graph.blocks[marker]['opcode'] = 'control_if_else'
                graph.blocks[marker]['inputs']['SUBSTACK2'] = [2, missing]
                graph.blocks[missing]['parent'] = marker
            patched.append(target['name'] + ': ' + code)
        count = _guard_cached_scene_writes(graph)
        if count:
            cached_writes[target['name']] = count
        separated = _separate_shared_reporters(graph)
        if separated:
            shared_reporters[target['name']] = separated
    if not patched:
        raise ValueError('BASE has no supported scene editor procedures')
    report = {'version': 3, 'guarded_procedures': patched,
              'guarded_cached_writes': cached_writes,
              'separated_shared_reporters': shared_reporters,
              'missing_object_behavior': 'Leave scene records unchanged'}
    project.setdefault('extensionData', {})['phase2_scene_lookup_guards'] = report
    return report
