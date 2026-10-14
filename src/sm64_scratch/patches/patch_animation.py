"""Assetless, idempotent Scratch logic fixes for the ROM-built Goddard intro.

Only block graphs and empty working buffers are added here. No geometry,
animation samples, texture bytes or decompilation source are bundled.
"""
from __future__ import annotations

import copy
import json
import zipfile
from sm64_scratch.render_targets import intro_render_target
from pathlib import Path

SPARK_TARGET = 'Title Intro Grab Sparkles'
PREFIX = 'v13_anim_'


class Graph:
    """Small Scratch 3 graph writer with explicit parent/next links."""
    def __init__(self, target, stage):
        self.target, self.stage = target, stage
        self.blocks = target['blocks']
        target_prefix = PREFIX + target['name'].replace(' ', '_') + '_'
        self.counter = max((int(key[len(target_prefix):]) for key in self.blocks
                            if key.startswith(target_prefix)
                            and key[len(target_prefix):].isdigit()), default=0)

    def node(self, opcode, inputs=None, fields=None, **extra):
        self.counter += 1
        key = PREFIX + self.target['name'].replace(' ', '_') + '_' + str(self.counter)
        self.blocks[key] = dict(opcode=opcode, next=None, parent=None,
                                inputs={}, fields=fields or {}, shadow=False,
                                topLevel=False, **extra)
        for name, value in (inputs or {}).items():
            if isinstance(value, str) and value in self.blocks:
                boolean = (opcode.startswith('control_') and name == 'CONDITION') or name.startswith('OPERAND') and opcode in ('operator_and', 'operator_or', 'operator_not')
                self.blocks[key]['inputs'][name] = [2, value] if boolean or name.startswith('SUBSTACK') else [3, value, [10, '']]
                self.blocks[value]['parent'] = key
            elif isinstance(value, tuple) and value[0] == 'var':
                self.blocks[key]['inputs'][name] = [3, [12, value[1], value[2]], [10, '']]
            else:
                self.blocks[key]['inputs'][name] = [1, [10, str(value)]]
        return key

    def var(self, name, default=0, local=False):
        owner = self.target if local else self.stage
        for key, definition in owner['variables'].items():
            if definition[0] == name:
                return ('var', name, key)
        key = PREFIX + ('local_' if local else '') + name.replace('.', '_')
        if key in owner.get('lists', {}):
            key += '_scalar'
        owner['variables'][key] = [name, default]
        return ('var', name, key)

    def list(self, name):
        for key, definition in self.stage['lists'].items():
            if definition[0] == name:
                return [name, key]
        key = PREFIX + name.replace('.', '_')
        if key in self.stage.get('variables', {}):
            key += '_list'
        self.stage['lists'][key] = [name, []]
        return [name, key]

    def set(self, var, value, change=False):
        return self.node('data_changevariableby' if change else 'data_setvariableto',
                         {'VALUE': value}, {'VARIABLE': list(var[1:])})

    def item(self, name, index):
        return self.node('data_itemoflist', {'INDEX': index}, {'LIST': self.list(name)})

    def length(self, name):
        return self.node('data_lengthoflist', fields={'LIST': self.list(name)})

    def op(self, opcode, a, b=None):
        if opcode == 'random':
            inputs = {'FROM': a, 'TO': b}
        elif opcode in ('and', 'or', 'equals', 'gt', 'lt'):
            inputs = {'OPERAND1': a, 'OPERAND2': b}
        elif opcode == 'not':
            inputs = {'OPERAND': a}
        else:
            inputs = {'NUM1': a, 'NUM2': b}
        return self.node('operator_' + opcode, inputs)

    def math(self, func, value):
        # Rounding is a separate Scratch reporter, not an option in mathop.
        if func == 'round':
            return self.node('operator_round', {'NUM': value})
        return self.node('operator_mathop', {'NUM': value}, {'OPERATOR': [func, None]})

    def chain(self, sequence, parent=None):
        for i, key in enumerate(sequence):
            self.blocks[key]['parent'] = sequence[i-1] if i else parent
            self.blocks[key]['next'] = sequence[i+1] if i+1 < len(sequence) else None
        return sequence[0] if sequence else None

    def control(self, opcode, condition, body, other=None):
        key = self.node(opcode, {'TIMES' if opcode == 'control_repeat' else 'CONDITION': condition})
        self.blocks[key]['inputs']['SUBSTACK'] = [2, self.chain(body, key)]
        if other is not None:
            self.blocks[key]['inputs']['SUBSTACK2'] = [2, self.chain(other, key)]
        return key

    def procedure(self, code, body):
        definition = self.node('procedures_definition')
        prototype = self.node('procedures_prototype')
        self.blocks[definition].update(topLevel=True, x=1000, y=self.counter * 8,
                                       inputs={'custom_block': [1, prototype]},
                                       next=self.chain(body, definition))
        self.blocks[prototype].update(parent=definition, shadow=True, mutation={
            'tagName': 'mutation', 'children': [], 'proccode': code,
            'argumentids': '[]', 'argumentnames': '[]', 'argumentdefaults': '[]', 'warp': 'true'})

    def call(self, code):
        key = self.node('procedures_call')
        self.blocks[key]['mutation'] = {'tagName': 'mutation', 'children': [],
                                       'proccode': code, 'argumentids': '[]', 'warp': 'true'}
        return key

    def insert_after(self, anchor, body):
        following = self.blocks[anchor]['next']
        first = self.chain(body, anchor)
        self.blocks[anchor]['next'] = first
        self.blocks[body[-1]]['next'] = following
        if following:
            self.blocks[following]['parent'] = body[-1]

    def hat(self, event, body, broadcast=None):
        key = self.node(event, fields={'BROADCAST_OPTION': broadcast} if broadcast else {})
        self.blocks[key].update(topLevel=True, x=0, y=self.counter * 8,
                                next=self.chain(body, key))


def _pose_cache(g):
    valid = g.var('TitleIntro.AnimatedPoseSaved')
    index = g.var('TitleIntro.i')
    vertex = g.var('TitleIntro.RuntimeVertex')
    first = g.var('TitleIntro.HeadFirstVertex')
    restore, capture = [], []
    for group in ('Affected', 'Eye'):
        source = 'TitleIntro.' + group + 'Vertex'
        restore_body = [g.set(vertex, g.op('add', first, g.op('subtract', g.item(source, index), 1)))]
        capture_body = [g.set(vertex, g.op('add', first, g.op('subtract', g.item(source, index), 1)))]
        for axis in 'XYZ':
            cache = 'TitleIntro.Animated' + group + axis
            restore_body.append(g.node('data_replaceitemoflist',
                {'INDEX': vertex, 'ITEM': g.item(cache, index)}, {'LIST': g.list('v' + axis.lower())}))
            capture.append(g.node('data_deletealloflist', fields={'LIST': g.list(cache)}))
            capture_body.append(g.node('data_addtolist', {'ITEM': g.item('v' + axis.lower(), vertex)},
                                       {'LIST': g.list(cache)}))
        restore_body.append(g.set(index, 1, True))
        capture_body.append(g.set(index, 1, True))
        restore.extend([g.set(index, 1), g.control('control_repeat', g.length(source), restore_body)])
        capture.extend([g.set(index, 1), g.control('control_repeat', g.length(source), capture_body)])
    restore.append(g.set(valid, 0))
    capture.append(g.set(valid, 1))
    g.procedure('TITLE | restore animated pose before grab',
                [g.control('control_if', g.op('equals', valid, 1), restore)])
    g.procedure('TITLE | save animated pose before grab', capture)

    # Remove the fixed-base reset. The animation is already evaluated before
    # this call. A second call without a frame update restores its saved pose.
    b = g.blocks
    b['tic_titleintro_b_00361']['next'] = g.chain([
        g.call('TITLE | restore animated pose before grab'),
        g.call('TITLE | save animated pose before grab'),
    ], 'tic_titleintro_b_00361')
    last = b[b['tic_titleintro_b_00361']['next']]['next']
    b[last]['next'] = 'tic_titleintro_b_00385'
    b['tic_titleintro_b_00385']['parent'] = last
    # Delete the former reset loop and all of its reporters.
    for number in range(363, 385):
        b.pop(f'tic_titleintro_b_{number:05d}', None)
    for block in b.values():
        if block['opcode'] == 'data_itemoflist':
            name = block.get('fields', {}).get('LIST', [None])[0]
            if name in ('TitleIntro.EyeBaseX', 'TitleIntro.EyeBaseY', 'TitleIntro.EyeBaseZ'):
                block['fields']['LIST'] = g.list(name.replace('EyeBase', 'AnimatedEye'))
    g.insert_after('tic_v9_head_set', [g.call('TITLE | restore animated pose before grab')])
    g.insert_after('tic_titleintro_b_00749', [g.set(valid, 0)])


def _burst_origin(g):
    """Rotate the selected joint + its spring offset with the ROM root pose."""
    selected = g.var('TitleIntro.SelectedGrab')
    frame = g.var('TitleIntro.AnimFrame')
    v = {key: g.var('TitleIntro.Burst' + key) for key in ('X','Y','Z','Seq','LX','LY','LZ','Y1','Z1','X2','Z2')}
    body = []
    for axis in 'XYZ':
        body.append(g.set(v['L'+axis], g.op('add', g.item('TitleIntro.GrabBase'+axis, selected),
                                           g.item('TitleIntro.GrabOffset'+axis, selected))))
    def trig(func, axis):
        return g.math(func, g.item('TitleIntro.RootR'+axis, frame))
    def mul(a, b):
        return g.op('multiply', a, b)
    body += [
        # Match Render's qX*qY*qZ matrix (point rotations Z, Y, X).
        g.set(v['Y1'], g.op('add', mul(v['LX'], trig('sin','Z')), mul(v['LY'], trig('cos','Z')))),
        g.set(v['Z1'], g.op('subtract', mul(v['LX'], trig('cos','Z')), mul(v['LY'], trig('sin','Z')))),
        g.set(v['X2'], g.op('add', mul(v['Z1'], trig('cos','Y')), mul(v['LZ'], trig('sin','Y')))),
        g.set(v['Z2'], g.op('subtract', mul(v['LZ'], trig('cos','Y')), mul(v['Z1'], trig('sin','Y')))),
        g.set(v['X'], g.op('add', g.item('TitleIntro.RootX',frame), v['X2'])),
        g.set(v['Y'], g.op('add', g.item('TitleIntro.RootY',frame), g.op('subtract', mul(v['Y1'],trig('cos','X')), mul(v['Z2'],trig('sin','X'))))),
        g.set(v['Z'], g.op('add', g.item('TitleIntro.RootZ',frame), g.op('add', mul(v['Y1'],trig('sin','X')), mul(v['Z2'],trig('cos','X'))))),
        g.set(v['Seq'], 1, True),
    ]
    g.insert_after('tic_titleintro_b_00314', body)
    g.insert_after('tic_titleintro_b_00749', [g.set(v['Seq'], 0)])


def _grab_particles(project, stage):
    source = next(t for t in project['targets'] if t['name'] == 'Title Intro Silver Sparkle2')
    existing = next((t for t in project['targets'] if t['name'] == SPARK_TARGET), None)
    if existing is not None:
        source = existing
        project['targets'].remove(existing)
    target = {key: copy.deepcopy(value) for key, value in source.items()}
    target.update(name=SPARK_TARGET, variables={}, lists={}, broadcasts={}, blocks={}, comments={},
                  sounds=[], currentCostume=0, visible=False, size=100,
                  layerOrder=max(t.get('layerOrder', 0) for t in project['targets']) + 1)
    for costume in target['costumes']:
        costume['name'] = costume['name'].replace('silver_', 'grab_')
        # C's 64-unit quad is centred in X and begins at Y=0.
        costume['rotationCenterX'], costume['rotationCenterY'] = 16, 32
        costume['bitmapResolution'] = 1
    project['targets'].append(target)
    g = Graph(target, stage)
    v = {name: g.var('GrabSpark.'+name, local=True) for name in
         ('Clone','Life','X','Y','Z','VX','VY','VZ','Tick','Seen','Depth')}
    active, tick, seq = (g.var('TitleIntro.'+n) for n in ('Active','GlobalTimer','BurstSeq'))
    def random(a, b):
        return g.op('random', a, b)
    def square(a):
        return g.op('multiply', a, a)
    def draw():
        return [g.set(v['Depth'], g.op('subtract', 2000, v['Z'])),
            g.control('control_if_else', g.op('gt', v['Depth'], 256), [
                g.node('motion_gotoxy', {'X': g.op('divide', g.op('multiply',v['X'],384),v['Depth']),
                                        'Y': g.op('divide',g.op('multiply',g.op('subtract',v['Y'],200),384),v['Depth'])}),
                g.node('looks_setsizeto', {'SIZE': g.op('divide',76800,v['Depth'])}),
                g.node('looks_switchcostumeto', {'COSTUME': v['Life']}),
                # The original indexed DL selects white primitive colour.
                # Preserve the ROM pixels, with no additive brightness.
                g.node('looks_seteffectto', {'VALUE': 0}, {'EFFECT':['BRIGHTNESS',None]}),
                g.node('looks_show'),
            ], [g.node('looks_hide')])]
    random_velocity = [g.set(v['V'+a], random(-25.0,25.0)) for a in 'XYZ']
    rejection = g.op('gt', g.op('add',g.op('add',square(v['VX']),square(v['VY'])),square(v['VZ'])),900)
    # Sample once, then reject only values outside the 30-unit sphere.
    spawn = [g.set(v['Life'], g.math('floor', g.op('subtract',12,random(0.0,5.0))))]
    spawn += [g.set(v[a],g.var('TitleIntro.Burst'+a)) for a in 'XYZ']
    spawn += random_velocity
    spawn += [g.control('control_repeat_until', g.op('not',rejection),
                        [g.set(v['V'+a],random(-25.0,25.0)) for a in 'XYZ']),
              g.set(v['VZ'],50,True)]
    move = [g.set(v[a],v['V'+a],True) for a in 'XYZ']
    move += [g.set(v['VY'],-0.4,True)]
    move += [g.set(v['V'+a],g.op('multiply',v['V'+a],0.9)) for a in 'XYZ']
    move += [g.set(v['Life'],-1,True),
             g.control('control_if_else',g.op('gt',v['Life'],0),draw(),[g.node('looks_hide')])]
    # These loops must be atomic at the current logical tick. A normal
    # Scratch repeat would create the pool across 30 execution frames, and
    # velocity rejection would delay a subset of the particles.
    g.procedure('TITLE | initialize grab sparkle pool',[
        g.control('control_repeat',30,[g.node('control_create_clone_of',{'CLONE_OPTION':'_myself_'})])])
    g.procedure('TITLE | move selected-joint sparkle',move)
    g.procedure('TITLE | emit selected-joint sparkle',spawn+[
        g.call('TITLE | move selected-joint sparkle')])
    loop = [g.node('control_wait_until', {'CONDITION': g.op('or',g.op('equals',active,0),
        g.op('or',g.op('gt',tick,v['Tick']),g.op('not',g.op('equals',seq,v['Seen']))))}),
        g.control('control_if',g.op('equals',active,1),[
            g.control('control_if',g.op('gt',tick,v['Tick']),[
                g.control('control_if',g.op('gt',v['Life'],0),[g.call('TITLE | move selected-joint sparkle')]),g.set(v['Tick'],tick)]),
            g.control('control_if',g.op('not',g.op('equals',seq,v['Seen'])),[
                g.set(v['Seen'],seq),g.control('control_if',g.op('lt',v['Life'],1),[g.call('TITLE | emit selected-joint sparkle')])])])]
    g.hat('control_start_as_clone',[g.set(v['Clone'],1),g.node('looks_hide'),g.set(v['Life'],0),
        g.set(v['Tick'],tick),g.set(v['Seen'],seq),g.node('looks_cleargraphiceffects'),
        g.node('looks_gotofrontback',fields={'FRONT_BACK':['front',None]}),
        g.control('control_repeat_until',g.op('equals',active,0),loop),g.node('control_delete_this_clone')])
    g.hat('event_whenbroadcastreceived',[
        g.control('control_if',g.op('equals',v['Clone'],0),[g.node('looks_hide'),
            g.call('TITLE | initialize grab sparkle pool')])],
        ['TITLE INTRO HEAD','titleintro_bc_head'])
    g.hat('event_whenflagclicked',[g.node('control_delete_this_clone'),g.set(v['Clone'],0),g.node('looks_hide')])
    g.hat('event_whenbroadcastreceived',[g.node('looks_hide'),g.node('control_delete_this_clone')],
          ['TITLE INTRO END','titleintro_bc_end'])


def _upgrade_continuity(controller, stage):
    """Upgrade existing graphs without rebuilding their pose-cache helpers."""
    g = Graph(controller, stage)
    # The US game advances these ROM samples at 30 Hz. Timing debt must not
    # turn slow loading/rendering into a burst of invisible intermediate poses.
    _upgrade_clock(controller, stage)
    g = Graph(controller, stage)
    # Old procedures had baked counts calibrated for private reference lists.
    # ROM rebuilds and future source updates must use the actual list length.
    for jump in ('809to750', '819to69', '659to661'):
        for axis in 'xyz':
            block = 'tic_v21_jump_' + jump + '_' + axis + '_rep'
            existing = g.blocks[block]['inputs'].get('TIMES', [])
            if (len(existing) > 1 and isinstance(existing[1], str)
                    and g.blocks.get(existing[1], {}).get('opcode') == 'data_lengthoflist'):
                continue
            length = g.length(f'TitleIntro.Jump{jump}{axis.upper()}Vertex')
            g.blocks[block]['inputs']['TIMES'] = [3, length, [10, '0']]
            g.blocks[length]['parent'] = block


def _upgrade_clock(controller, stage):
    g = Graph(controller, stage)
    clock = g.var('TitleIntro.NextFrameTime')
    period = 1 / 30
    for phase in ('logo', 'hold', 'head'):
        anchor = 'tic_v9_' + phase + '_set'
        g.blocks['tic_v9_' + phase + '_add']['inputs']['NUM2'] = [1, [4, str(period)]]
        marker = 'v13_clock_guard_' + phase
        if marker in g.blocks:
            continue
        guard = g.control('control_if',
            g.op('gt', g.node('sensing_timer'), g.op('add', clock, period)),
            [g.set(clock, g.node('sensing_timer'))])
        # Stable marker also makes upgrades idempotent after graph generation.
        block = g.blocks.pop(guard)
        for value in block['inputs'].values():
            if len(value) > 1 and isinstance(value[1], str):
                g.blocks[value[1]]['parent'] = marker
        previous = g.blocks[anchor]['parent']
        for candidate in g.blocks.values():
            if candidate.get('next') == anchor:
                candidate['next'] = marker
            for value in candidate.get('inputs', {}).values():
                if len(value) > 1 and value[1] == anchor:
                    value[1] = marker
        block.update(parent=previous, next=anchor)
        g.blocks[marker] = block
        g.blocks[anchor]['parent'] = marker
    # The non-warp loop and renderer already yield for stage presentation.
    # An extra wait(0) costs a third 60 Hz scheduler slot in TurboWarp's
    # compiled runtime, reducing a 30 Hz animation to about 20 Hz.
    for phase in ('logo', 'head'):
        marker = 'v13_clock_present_' + phase
        block = g.blocks.pop(marker, None)
        if block:
            previous, following = block['parent'], block['next']
            g.blocks[previous]['next'] = following
            if following:
                g.blocks[following]['parent'] = previous


def _upgrade_entry_presentation(project, controller, stage):
    """Pair the C head sample with its wipe sample before each render.

    The old independent sprite loop advanced on GlobalTimer *after* drawing:
    presentation could combine head N with wipe N+1, including hiding the
    wipe on head 20. C draws head 1..20 under transition samples 0..19.
    Also measure actual completed intro renders; Main Loop waits for the
    entire intro and cannot update its time.fps variable during this phase.
    """
    # Schedule both handlers in the same render tick. Waiting for the
    # costume change yields on its redraw and delays the head by one tick.
    for block in controller['blocks'].values():
        if (block['opcode'] == 'event_broadcastandwait'
                and 'TITLE INTRO REVEAL STEP' in str(block.get('inputs'))):
            block['opcode'] = 'event_broadcast'
    if 'v13_entry_presentation' in controller['blocks']:
        return
    g = Graph(controller, stage)
    frame = g.var('TitleIntro.Frame')
    fps = g.var('time.fps')
    window = g.var('TitleIntro.FPSWindowStart', -1)
    count = g.var('TitleIntro.FPSIntervals')
    now = g.var('TitleIntro.FPSNow')
    g.procedure('TITLE | reset measured render FPS', [
        g.set(window, -1), g.set(count, 0), g.set(fps, 0)])
    elapsed = lambda: g.op('subtract', now, window)
    g.procedure('TITLE | count completed render', [
        g.set(now, g.node('sensing_timer')),
        g.control('control_if_else', g.op('lt', window, 0), [g.set(window, now)], [
            g.set(count, 1, True),
            g.control('control_if', g.op('gt', elapsed(), 0.499999), [
                g.set(fps, g.node('operator_round', {'NUM': g.op('divide', count, elapsed())})),
                g.set(window, now), g.set(count, 0)])])])
    # Reset only after model preparation, so loading time is not counted as
    # a slow frame. Both phases retain the existing nominal 30 Hz clock.
    g.insert_after('tic_titleintro_b_00694', [g.call('TITLE | reset measured render FPS')])
    g.insert_after('tic_titleintro_b_00752', [g.call('TITLE | reset measured render FPS')])
    for anchor in ('tic_titleintro_b_00711', 'tic_titleintro_b_00759'):
        g.insert_after(anchor, [g.call('TITLE | count completed render')])
    # Use one live monitor bound to the real variable ID. The old capture
    # monitor had an unrelated ID and kept displaying a saved value of 24.
    stage['variables'][fps[2]][1] = 0
    monitor = next((m for m in project.get('monitors', [])
                    if m.get('params', {}).get('VARIABLE') == 'time.fps'), None)
    project['monitors'] = [m for m in project.get('monitors', [])
                           if m.get('params', {}).get('VARIABLE') != 'time.fps']
    project['monitors'].append(dict(monitor or {}, id=fps[2], opcode='data_variable',
        mode='default', params={'VARIABLE': 'time.fps'}, spriteName=None,
        value=0, visible=True, x=5, y=320, width=0, height=0,
        sliderMin=0, sliderMax=100, isDiscrete=True))
    g.blocks['tic_titleintro_b_00675']['opcode'] = 'data_showvariable'

    reveal = next(t for t in project['targets'] if t['name'] == 'Title Intro Star Reveal')
    reveal['blocks'] = {}
    sg = Graph(reveal, stage)
    def costume(value):
        return sg.node('looks_switchcostumeto', {'COSTUME': value})
    sg.hat('event_whenbroadcastreceived', [costume(1), sg.node('looks_show')],
           ['TITLE INTRO REVEAL', 'titleintro_bc_reveal'])
    step_id = 'v13_bc_reveal_step'
    stage['broadcasts'][step_id] = 'TITLE INTRO REVEAL STEP'
    sg.hat('event_whenbroadcastreceived', [
        sg.control('control_if_else', sg.op('lt', frame, 20),
            [costume(sg.op('add', frame, 1)), sg.node('looks_show')],
            [sg.node('looks_hide')])], ['TITLE INTRO REVEAL STEP', step_id])
    sg.hat('event_whenflagclicked', [sg.node('looks_hide')])
    sg.hat('event_whenbroadcastreceived', [sg.node('looks_hide')],
           ['TITLE INTRO END', 'titleintro_bc_end'])
    prepare = g.node('event_broadcast')
    g.blocks[prepare]['inputs'] = {'BROADCAST_INPUT': [1, [11, 'TITLE INTRO REVEAL STEP', step_id]]}
    body = g.control('control_if', g.op('lt', frame, 21), [prepare])
    marker = 'v13_entry_presentation'
    g.blocks[marker] = g.blocks.pop(body)
    for value in g.blocks[marker]['inputs'].values():
        if len(value) > 1 and isinstance(value[1], str):
            g.blocks[value[1]]['parent'] = marker
    g.insert_after('tic_titleintro_b_00771', [marker])


def _upgrade_goddard_depth_clip(project, stage):
    """Use Goddard's 30..5000 view interval for the title head.

    The root animation starts at Z=-20010. The generic Scratch rasterizer
    had only a near plane, so it drew a red speck that the N64 far plane
    rejects. Clip camera-space triangles, including interpolated UVs; do
    not delay, hide or rewrite the ROM animation.
    """
    target = intro_render_target(project)
    g = Graph(target, stage)
    if 'v13_goddard_depth_clip' in g.blocks:
        return
    proto_id, proto = next((k, b) for k, b in g.blocks.items()
                          if b['opcode'] == 'procedures_prototype'
                          and b['mutation']['proccode'].startswith('RASTER | clip near '))
    definition = g.blocks[proto['parent']]
    original_body = definition['next']
    names = json.loads(proto['mutation']['argumentnames'])
    ids = json.loads(proto['mutation']['argumentids'])
    original_code = proto['mutation']['proccode']
    internal_code = original_code.replace('clip near ', 'clip near internal ', 1)
    far_code = original_code.replace('clip near %s ', 'clip far %s near %s ', 1)
    near_id = 'v13_goddard_near_arg'

    def argument(name):
        return g.node('argument_reporter_string_number', fields={'VALUE': [name, None]})

    def call(code, arg_ids, values):
        node = g.node('procedures_call', dict(zip(arg_ids, values)))
        g.blocks[node]['mutation'] = dict(tagName='mutation', children=[],
            proccode=code, argumentids=json.dumps(arg_ids), warp='true')
        return node

    def make_definition(code, arg_ids, arg_names, body):
        d = g.node('procedures_definition', x=1300, y=g.counter * 8)
        g.blocks[d]['topLevel'] = True
        p = g.node('procedures_prototype',
                   mutation=dict(tagName='mutation', children=[], proccode=code,
                     argumentids=json.dumps(arg_ids), argumentnames=json.dumps(arg_names),
                     argumentdefaults=json.dumps([''] * len(arg_ids)), warp='true'))
        g.blocks[p].update(parent=d, shadow=True)
        g.blocks[d]['inputs'] = {'custom_block': [1, p]}
        g.blocks[d]['next'] = body
        g.blocks[body]['parent'] = d

    # Clone the existing eight-case triangle clipper with reversed depth
    # comparisons. Its interpolation expressions remain valid for far-Z.
    originals = copy.deepcopy(g.blocks)
    mapping = {}
    far_t = {n: g.var('GoddardClip.' + n, local=True) for n in ('t1', 't2')}
    def clone(key):
        if key in mapping:
            return mapping[key]
        old = originals[key]
        new = g.node(old['opcode'])
        mapping[key] = new
        block = copy.deepcopy(old)
        block.update(parent=None, next=None, topLevel=False)
        for value in block['inputs'].values():
            if len(value) > 1 and isinstance(value[1], str):
                value[1] = clone(value[1])
                g.blocks[value[1]]['parent'] = new
        if old.get('next'):
            block['next'] = clone(old['next'])
            g.blocks[block['next']]['parent'] = new
        if block['opcode'] == 'operator_gt':
            # A triangle exactly on the far plane remains inside.
            outside = g.node('operator_gt')
            g.blocks[outside].update(parent=new, inputs=block['inputs'])
            for value in block['inputs'].values():
                if isinstance(value[1], str):
                    g.blocks[value[1]]['parent'] = outside
            block['opcode'] = 'operator_not'
            block['inputs'] = {'OPERAND': [2, outside]}
        if block['opcode'] == 'argument_reporter_string_number' and block['fields']['VALUE'][0] == 'near':
            block['fields']['VALUE'][0] = 'far'
        variable = block.get('fields', {}).get('VARIABLE')
        if variable and variable[0] in far_t:
            block['fields']['VARIABLE'] = list(far_t[variable[0]][1:])
        for value in block['inputs'].values():
            if len(value) > 1 and isinstance(value[1], list) and value[1][0] == 12 and value[1][1] in far_t:
                value[1] = [12, *far_t[value[1][1]][1:]]
        g.blocks[new] = block
        if block.get('mutation', {}).get('proccode', '').startswith('RASTER | draw triangle '):
            old_inputs = list(block['inputs'].values())
            values = []
            for i, value in enumerate(old_inputs):
                if i % 5 in (0, 1):
                    # Strip perspective projection; the near clipper needs
                    # camera XYZ and will project after both planes.
                    projected = g.blocks[value[1]]
                    value = copy.deepcopy(projected['inputs']['NUM1'])
                values.append(value)
            block['mutation'].update(proccode=internal_code, argumentids=json.dumps(ids))
            block['inputs'] = {ids[0]: [3, argument('near'), [10, '']]}
            block['inputs'].update(zip(ids[1:], values))
            for value in block['inputs'].values():
                if len(value) > 1 and isinstance(value[1], str):
                    g.blocks[value[1]]['parent'] = new
        return new
    far_body = clone(original_body)
    make_definition(far_code, [ids[0], near_id, *ids[1:]], ['far', 'near', *names[1:]], far_body)
    make_definition(internal_code, ids, names, original_body)
    model_name = g.item('Loaded models', g.op('add', g.var('engine.modelindex'), 1))
    selected = g.op('and', g.op('equals', g.var('TitleIntro.Active'), 1),
                  g.op('and', g.op('equals', g.var('TitleIntro.Phase'), 2),
                       g.op('equals', model_name, 'TitleIntro Mario Head')))
    far_call = call(far_code, [ids[0], near_id, *ids[1:]], [5000, 30, *[argument(n) for n in names[1:]]])
    normal_call = call(internal_code, ids, [argument(n) for n in names])
    wrapper = g.control('control_if_else', selected, [far_call], [normal_call])
    g.blocks['v13_goddard_depth_clip'] = g.blocks.pop(wrapper)
    for value in g.blocks['v13_goddard_depth_clip']['inputs'].values():
        if isinstance(value[1], str):
            g.blocks[value[1]]['parent'] = 'v13_goddard_depth_clip'
    definition['next'] = 'v13_goddard_depth_clip'
    g.blocks['v13_goddard_depth_clip']['parent'] = proto['parent']


def _upgrade_title_copyright(project, controller, stage):
    """Fade the existing ROM decal on the presented logo frame, as in C.

    The logo callback increments its zero-based counter before copyright is
    drawn. Alpha is drawn first, then incremented by 26 when counter >=19:
    Scratch frames 1..19 are transparent, frame20 is 26, frame29 is255.
    Updating by a separate GlobalTimer loop can lag behind presentation.
    """
    marker = 'v13_copyright_presentation'
    if marker in controller['blocks']:
        return
    target = next(t for t in project['targets'] if t['name'] == 'Title Intro Copyright TM')
    target['blocks'] = {}
    target['variables'] = {}
    target.update(x=0, y=0, size=100, direction=90, visible=False, currentCostume=0,
                  rotationStyle='don\'t rotate')
    sg = Graph(target, stage)
    frame = sg.var('TitleIntro.Frame')
    reset = lambda: [sg.node('looks_cleargraphiceffects'),
                     sg.node('looks_seteffectto', {'VALUE': 100}, {'EFFECT': ['ghost', None]}),
                     sg.node('looks_show')]
    sg.hat('event_whenflagclicked', [sg.node('looks_hide')])
    sg.hat('event_whenbroadcastreceived', reset(), ['TITLE INTRO LOGO', 'titleintro_bc_logo'])
    step_id = 'v13_bc_copyright_step'
    stage['broadcasts'][step_id] = 'TITLE INTRO COPYRIGHT STEP'
    alpha = sg.op('multiply', sg.op('subtract', frame, 19), 26)
    ghost = sg.op('subtract', 100, sg.op('divide', alpha, 2.55))
    update = sg.control('control_if_else', sg.op('gt', frame, 28), [
        sg.node('looks_seteffectto', {'VALUE': 0}, {'EFFECT': ['ghost', None]})], [
        sg.control('control_if_else', sg.op('gt', frame, 19), [
            sg.node('looks_seteffectto', {'VALUE': ghost}, {'EFFECT': ['ghost', None]})], [
            sg.node('looks_seteffectto', {'VALUE': 100}, {'EFFECT': ['ghost', None]})])])
    sg.hat('event_whenbroadcastreceived', [update], ['TITLE INTRO COPYRIGHT STEP', step_id])
    for event, identity in (('TITLE INTRO HEAD', 'titleintro_bc_head'),
                            ('TITLE INTRO END', 'titleintro_bc_end')):
        sg.hat('event_whenbroadcastreceived', [sg.node('looks_hide')], [event, identity])
    g = Graph(controller, stage)
    broadcast = g.node('event_broadcast')
    g.blocks[broadcast]['inputs'] = {'BROADCAST_INPUT': [1, [11, 'TITLE INTRO COPYRIGHT STEP', step_id]]}
    g.blocks[marker] = g.blocks.pop(broadcast)
    # After the fade-out trigger, immediately before rendering the logo.
    g.insert_after('tic_titleintro_b_00705', [marker])
    project['extensionData']['v13_title_copyright'] = dict(
        version=1, source='ROM_US_MIO0_RGBA16', first_visible_logo_frame=20,
        fully_visible_logo_frame=29, alpha_step=26,
        presentation='before_each_logo_render', output_size=[480, 360])


def _upgrade_title_logo_fade(project, stage):
    """Present the native16-frame black fade above logo and copyright.

    Reuse the controller's before-render logo step. GlobalTimer is changed
    after drawing and its old independent loop could show a stale costume.
    Keep the final black frame through loading, until REVEAL begins.
    Media remains a transparent placeholder in BASE; constructors supply
    the black quad frames. This graph adds no protected pixels.
    """
    metadata = project['extensionData']
    if metadata.get('v13_title_logo_fade', {}).get('version') == 1:
        fade = next(t for t in project['targets'] if t['name'] == 'Title Intro Logo Fade')
        if 'v13_logo_fade_presentation' in fade['blocks']:
            return
    fade = next(t for t in project['targets'] if t['name'] == 'Title Intro Logo Fade')
    fade['blocks'] = {}
    fade['variables'] = {}
    fade.update(x=0, y=0, size=100, direction=90, visible=False, currentCostume=0,
                rotationStyle='don\'t rotate')
    g = Graph(fade, stage)
    frame = g.var('TitleIntro.Frame')
    update = g.control('control_if_else', g.op('gt', frame, 75), [
        g.node('looks_switchcostumeto', {'COSTUME': g.op('subtract', frame, 75)}),
        g.node('looks_show')], [g.node('looks_hide')])
    g.hat('event_whenbroadcastreceived', [update],
          ['TITLE INTRO COPYRIGHT STEP', 'v13_bc_copyright_step'])
    g.blocks['v13_logo_fade_presentation'] = g.blocks.pop(update)
    # Move references to the stable marker without leaving dangling parents.
    for block in g.blocks.values():
        if block.get('parent') == update:
            block['parent'] = 'v13_logo_fade_presentation'
        if block.get('next') == update:
            block['next'] = 'v13_logo_fade_presentation'
    g.hat('event_whenflagclicked', [g.node('looks_hide')])
    g.hat('event_whenbroadcastreceived', [g.node('looks_cleargraphiceffects'), g.node('looks_hide')],
          ['TITLE INTRO LOGO', 'titleintro_bc_logo'])
    for event, identity in (('TITLE INTRO REVEAL', 'titleintro_bc_reveal'),
                            ('TITLE INTRO END', 'titleintro_bc_end')):
        g.hat('event_whenbroadcastreceived', [g.node('looks_hide')], [event, identity])
    metadata['v13_title_logo_fade'] = dict(version=1, frames=16,
        logo_frames=[76, 91], alpha='round(t*255/15)',
        presentation='before_each_logo_render', includes_copyright_tm=True,
        holds_black_until_reveal=True)


def apply_animation_fixes(project):
    """Mutate a project graph, preserving all existing media and ROM lists."""
    metadata = project.setdefault('extensionData', {})
    if metadata.get('intro_only_base'):
        if metadata.get('v13_animation_grab_sparkles', {}).get('version') != 7:
            raise ValueError('Regenerate the intro base from an updated BASE_FULL before patching.')
        from prepare_intro_base import validate_graph
        validate_graph(project)
        return metadata['v13_animation_grab_sparkles']
    stage = next(t for t in project['targets'] if t.get('isStage'))
    controller = next(t for t in project['targets'] if t['name'] == 'Title Intro Controller')
    _upgrade_goddard_depth_clip(project, stage)
    _upgrade_title_copyright(project, controller, stage)
    _upgrade_title_logo_fade(project, stage)
    # Synchronizing fresh private logic can replace the controller while
    # retaining BASE's project metadata. Only skip an actually patched graph.
    if (metadata.get('v13_animation_grab_sparkles', {}).get('version') in (1, 2, 3, 4, 5, 6, 7)
            and any(key.startswith(PREFIX) for key in controller['blocks'])
            and any(t['name'] == SPARK_TARGET for t in project['targets'])):
        result = metadata['v13_animation_grab_sparkles']
        if result['version'] == 1:
            _grab_particles(project, stage)
            result.update(version=2, grab_particle_update='atomic_30hz_pool_and_burst')
        _upgrade_continuity(controller, stage)
        _upgrade_entry_presentation(project, controller, stage)
        result.update(version=7, goddard_depth_clip=[30, 5000], head_animation_fps=30, logo_animation_fps=30,
                      head_reveal='head_1_to_20_under_star_0_to_19_before_render',
                      fps_monitor='measured_completed_intro_renders',
                      clock='30hz_bounded_debt_renderer_and_loop_yield',
                      face_jumps='dynamic_list_lengths',
                      grab_particle_update='atomic_30hz_pool_and_burst',
                      grab_particle_colour='rom_texture_times_white_primitive')
        from sm64_scratch.patches.patch_camera import apply_camera_controls
        apply_camera_controls(project)
        from sm64_scratch.patches.patch_effects import apply_effect_presentation
        apply_effect_presentation(project)
        return result
    g = Graph(controller, stage)
    clock = g.var('TitleIntro.NextFrameTime')
    for phase, anchor in (('logo','tic_titleintro_b_00694'),
                          ('hold','tic_titleintro_b_00695'),
                          ('head','tic_titleintro_b_00752')):
        reporter = g.blocks['tic_v9_'+phase+'_add']
        reporter['inputs']['NUM1'] = [3,[12,clock[1],clock[2]],[10,'']]
        g.insert_after(anchor,[g.set(clock,g.node('sensing_timer'))])
    forced = 'tic_v22_force_grab_state_if'
    if forced in g.blocks:
        previous, following = g.blocks[forced]['parent'], g.blocks[forced]['next']
        g.blocks[previous]['next'] = following
        g.blocks[following]['parent'] = previous
        # Remove only this detached subtree; the C animator decides when to
        # enter state 5. States 3 and 4 keep their facial sequence on a grab.
        def remove_tree(key):
            block = g.blocks.pop(key, None)
            if block:
                for value in block['inputs'].values():
                    if len(value)>1 and isinstance(value[1],str):
                        remove_tree(value[1])
        remove_tree(forced)
    _pose_cache(g)
    _burst_origin(g)
    _grab_particles(project, stage)
    _upgrade_continuity(controller, stage)
    _upgrade_entry_presentation(project, controller, stage)
    result = dict(version=7, goddard_depth_clip=[30, 5000], clock='30hz_bounded_debt_renderer_and_loop_yield',
                  head_reveal='head_1_to_20_under_star_0_to_19_before_render',
                  fps_monitor='measured_completed_intro_renders',
                  head_animation_fps=30, logo_animation_fps=30,
                  face_jumps='dynamic_list_lengths',
                  grab_particle_colour='rom_texture_times_white_primitive',
                  grab_pose='additive_on_current_animation',
                  grab_state_machine='shape_helper.c_mario_head_movement',
                  grab_particle_pool=30, grab_particle_trigger='once_per_successful_selection',
                  grab_particle_space='3d_joint_rotated_by_rom_root',
                  grab_particle_update='atomic_30hz_pool_and_burst',
                  sparkle_rom_offset='0x262088', sparkle_timeout_tiles=[4,4,3,3,2,2,1,1,0,0,4,4])
    metadata['v13_animation_grab_sparkles'] = result
    from sm64_scratch.patches.patch_camera import apply_camera_controls
    apply_camera_controls(project)
    from sm64_scratch.patches.patch_effects import apply_effect_presentation
    apply_effect_presentation(project)
    return result


def patch_file(path: Path):
    with zipfile.ZipFile(path) as source:
        project = json.loads(source.read('project.json'))
        result = apply_animation_fixes(project)
        temporary = path.with_name(path.name+'.animation.tmp')
        try:
            with zipfile.ZipFile(temporary,'w') as output:
                for item in source.infolist():
                    output.writestr(item,json.dumps(project,ensure_ascii=False,separators=(',',':')).encode()
                                    if item.filename=='project.json' else source.read(item.filename))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return result
