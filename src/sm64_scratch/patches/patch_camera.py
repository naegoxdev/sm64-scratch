"""Assetless Goddard camera controls and shared world-space projection.

The US camera uses 4-degree C-button steps, a +/-80-degree pitch limit,
three eye positions and 0.25 eye smoothing. ROM tracks are supplied by the
builder; no model, pixel or animation data is embedded in this module.
"""
from sm64_scratch.patches.patch_animation import Graph

RESET = 'TITLE | reset Goddard camera'
UPDATE = 'TITLE | update Goddard camera'
STARS = 'TITLE | project Goddard stars'
MARKER = 'v13_goddard_camera_controls'


def camera_vars(g):
    defaults = {'EyeX': 0, 'EyeY': 200, 'EyeZ': 2000,
                'RX': -1, 'RY': 0, 'RZ': 0, 'UX': 0, 'UY': 1, 'UZ': 0,
                'FX': 0, 'FY': 0, 'FZ': -1}
    return {n: g.var('TitleIntro.Camera' + n, d) for n, d in defaults.items()}


def dot(g, a, b):
    return g.op('add', g.op('add', g.op('multiply', a[0], b[0]),
                           g.op('multiply', a[1], b[1])), g.op('multiply', a[2], b[2]))


def projection(g, point, prefix, local=False, renderer_x=False):
    """Project a world point; mesh/grab X also includes Render's m! reflection.

    Star tracks retain their existing sprite convention. Mesh vertices have
    a final X reflection after the view matrix, before perspective division.
    """
    c = camera_vars(g)
    v = {n: g.var(prefix + n, local=local) for n in ('DX', 'DY', 'DZ', 'Depth', 'ScreenX', 'ScreenY')}
    body = [g.set(v['D' + a], g.op('subtract', point[i], c['Eye' + a])) for i, a in enumerate('XYZ')]
    delta = [v['D' + a] for a in 'XYZ']
    body.append(g.set(v['Depth'], dot(g, delta, [c['F' + a] for a in 'XYZ'])))
    body.append(g.control('control_if_else', g.op('gt', v['Depth'], 30), [
        g.set(v['ScreenX'], g.op('divide', g.op('multiply', dot(g, delta, [c['R' + a] for a in 'XYZ']), -384 if renderer_x else 384), v['Depth'])),
        g.set(v['ScreenY'], g.op('divide', g.op('multiply', dot(g, delta, [c['U' + a] for a in 'XYZ']), 384), v['Depth']))
    ], [g.set(v['ScreenX'], 10000), g.set(v['ScreenY'], 10000)]))
    return body, v


def world_point(g, index):
    """Same root transform as the existing selected-joint burst."""
    f = g.var('TitleIntro.AnimFrame')
    v = {n: g.var('TitleIntro.CameraJoint' + n) for n in ('LX', 'LY', 'LZ', 'Y1', 'Z1', 'X2', 'Z2', 'X', 'Y', 'Z')}
    mul = lambda a, b: g.op('multiply', a, b)
    trig = lambda fn, a: g.math(fn, g.item('TitleIntro.RootR' + a, f))
    body = [g.set(v['L' + a], g.op('add', g.item('TitleIntro.GrabBase' + a, index),
                                         g.item('TitleIntro.GrabOffset' + a, index))) for a in 'XYZ']
    body += [
        # Render's xyz quaternion is qX*qY*qZ: points rotate Z, Y, X.
        g.set(v['Y1'], g.op('add', mul(v['LX'], trig('sin', 'Z')), mul(v['LY'], trig('cos', 'Z')))),
        g.set(v['Z1'], g.op('subtract', mul(v['LX'], trig('cos', 'Z')), mul(v['LY'], trig('sin', 'Z')))),
        g.set(v['X2'], g.op('add', mul(v['Z1'], trig('cos', 'Y')), mul(v['LZ'], trig('sin', 'Y')))),
        g.set(v['Z2'], g.op('subtract', mul(v['LZ'], trig('cos', 'Y')), mul(v['Z1'], trig('sin', 'Y')))),
        g.set(v['X'], g.op('add', g.item('TitleIntro.RootX', f), v['X2'])),
        g.set(v['Y'], g.op('add', g.item('TitleIntro.RootY', f), g.op('subtract', mul(v['Y1'], trig('cos', 'X')), mul(v['Z2'], trig('sin', 'X'))))),
        g.set(v['Z'], g.op('add', g.item('TitleIntro.RootZ', f), g.op('add', mul(v['Y1'], trig('sin', 'X')), mul(v['Z2'], trig('cos', 'X')))))]
    return body, v


def _controls(controller, stage):
    g = Graph(controller, stage)
    c = camera_vars(g)
    v = {n: g.var('TitleIntro.Camera' + n) for n in ('Yaw', 'Pitch', 'Zoom', 'XWasDown', 'Q', 'TargetX', 'TargetY', 'TargetZ', 'Z1', 'Norm', 'Horizontal')}
    key = lambda name: g.node('sensing_keypressed', {'KEY_OPTION': name})
    mul = lambda a, b: g.op('multiply', a, b)
    defaults = {'EyeX': 0, 'EyeY': 200, 'EyeZ': 2000, 'RX': -1, 'RY': 0, 'RZ': 0,
                'UX': 0, 'UY': 1, 'UZ': 0, 'FX': 0, 'FY': 0, 'FZ': -1}
    g.procedure(RESET, [g.set(v[n], 0) for n in ('Yaw', 'Pitch', 'Zoom', 'XWasDown')]
                + [g.set(c[n], val) for n, val in defaults.items()])
    def sound(direction):
        bc = 'titleintro_bc_camera_zoom_' + direction
        name = 'TITLE INTRO CAMERA ZOOM ' + direction.upper()
        stage.setdefault('broadcasts', {})[bc] = name
        node = g.node('event_broadcast')
        g.blocks[node]['inputs'] = {'BROADCAST_INPUT': [1, [11, name, bc]]}
        return node
    controls = [g.control('control_if', g.op('and', key('x'), g.op('equals', v['XWasDown'], 0)), [
        g.set(v['Zoom'], g.op('mod', g.op('add', v['Zoom'], 1), 3)),
        g.control('control_if_else', g.op('equals', v['Zoom'], 0), [sound('in')], [sound('out')])]),
        g.control('control_if_else', key('x'), [g.set(v['XWasDown'], 1)], [g.set(v['XWasDown'], 0)])]
    for name, variable, delta in (('left arrow', 'Yaw', 4), ('right arrow', 'Yaw', -4),
                                  ('up arrow', 'Pitch', 4), ('down arrow', 'Pitch', -4)):
        controls.append(g.control('control_if', key(name), [g.set(v[variable], delta, True)]))
    controls += [g.control('control_if', g.op('gt', v['Pitch'], 80), [g.set(v['Pitch'], 80)]),
                 g.control('control_if', g.op('lt', v['Pitch'], -80), [g.set(v['Pitch'], -80)])]
    # C's zoom positions are q*(0,200,2000), with a fixed look-at (0,200,0).
    body = controls + [g.set(v['Q'], g.op('add', 1, mul(v['Zoom'], 0.5))),
        g.set(v['TargetY'], mul(v['Q'], g.op('subtract', mul(200, g.math('cos', v['Pitch'])), mul(2000, g.math('sin', v['Pitch']))))),
        g.set(v['Z1'], mul(v['Q'], g.op('add', mul(200, g.math('sin', v['Pitch'])), mul(2000, g.math('cos', v['Pitch']))))),
        g.set(v['TargetX'], mul(v['Z1'], g.math('sin', v['Yaw']))),
        g.set(v['TargetZ'], mul(v['Z1'], g.math('cos', v['Yaw'])))]
    for a in 'XYZ':
        body += [g.set(c['Eye' + a], mul(g.op('subtract', v['Target' + a], c['Eye' + a]), 0.25), True),
                 g.set(g.var('cam ' + a.lower()), c['Eye' + a])]
    body += [g.set(c['FX'], mul(c['EyeX'], -1)), g.set(c['FY'], g.op('subtract', 200, c['EyeY'])),
             g.set(c['FZ'], mul(c['EyeZ'], -1)),
             g.set(v['Norm'], g.math('sqrt', dot(g, [c['F' + a] for a in 'XYZ'], [c['F' + a] for a in 'XYZ'])))]
    body += [g.set(c['F' + a], g.op('divide', c['F' + a], v['Norm'])) for a in 'XYZ']
    body += [g.set(v['Horizontal'], g.math('sqrt', g.op('add', mul(c['FX'], c['FX']), mul(c['FZ'], c['FZ'])))),
             g.set(c['RX'], g.op('divide', c['FZ'], v['Horizontal'])), g.set(c['RY'], 0),
             g.set(c['RZ'], g.op('divide', mul(c['FX'], -1), v['Horizontal'])),
             g.set(c['UX'], mul(c['FY'], c['RZ'])),
             g.set(c['UY'], g.op('subtract', mul(c['FZ'], c['RX']), mul(c['FX'], c['RZ']))),
             g.set(c['UZ'], mul(mul(c['FY'], c['RX']), -1))]
    g.procedure(UPDATE, body)
    g.insert_after('tic_titleintro_b_00724', [g.call(RESET)])
    # Before the animator/grabber/renderer; one step per logical 30-Hz frame.
    call = g.call(UPDATE)
    g.blocks[MARKER] = g.blocks.pop(call)
    g.insert_after('tic_v9_head_set', [MARKER])


def _render_basis(project, stage):
    from sm64_scratch.render_targets import intro_render_target
    target = intro_render_target(project)
    if 'v13_goddard_camera_basis' in target['blocks']:
        return
    g = Graph(target, stage)
    c = camera_vars(g)
    body = [g.set(g.var('view mat' + str(i + 1)), c[n]) for i, n in enumerate(
            ('RX', 'RY', 'RZ', 'UX', 'UY', 'UZ', 'FX', 'FY', 'FZ'))]
    guard = g.control('control_if', g.op('and', g.op('equals', g.var('TitleIntro.Active'), 1),
                            g.op('equals', g.var('TitleIntro.Phase'), 2)), body)
    stable = 'v13_goddard_camera_basis'
    g.blocks[stable] = g.blocks.pop(guard)
    for value in g.blocks[stable]['inputs'].values():
        if isinstance(value[1], str) and value[1] in g.blocks:
            g.blocks[value[1]]['parent'] = stable
    g.insert_after('R}', [stable])


def _stars(controller, project, stage):
    g = Graph(controller, stage)
    f = g.var('TitleIntro.AnimFrame')
    body = []
    for colour in ('Silver', 'Red'):
        prefix = 'TitleIntro.' + colour + 'Star'
        world = [g.item(prefix + 'World' + a, f) for a in 'XYZ']
        work, v = projection(g, world, prefix + 'Camera', renderer_x=True)
        screen = {n: g.var(prefix + 'Screen' + n) for n in ('X', 'Y', 'Size', 'Visible')}
        body += work + [g.set(screen['X'], g.op('add', 160, g.op('divide', v['ScreenX'], 1.5))),
                        g.set(screen['Y'], g.op('subtract', 120, g.op('divide', v['ScreenY'], 1.5))),
                        g.set(screen['Visible'], 0), g.set(screen['Size'], 5)]
        condition = g.op('and', g.op('equals', g.item(prefix + 'Draw', f), 1),
                         g.op('and', g.op('gt', v['Depth'], 30), g.op('lt', v['Depth'], 5000.00001)))
        body.append(g.control('control_if', condition, [g.set(screen['Visible'], 1)]))
        body.append(g.control('control_if', g.op('gt', v['Depth'], 0), [
            g.set(screen['Size'], g.op('divide', 25600, v['Depth']))]))
        target = next(t for t in project['targets'] if t['name'] == 'Title Intro ' + colour + ' Star')
        for block in target['blocks'].values():
            if block['opcode'] == 'data_itemoflist':
                name = block['fields'].get('LIST', [''])[0]
                for suffix in ('X', 'Y', 'Size', 'Visible'):
                    if name == prefix + suffix:
                        block.update(opcode='data_variable', inputs={}, fields={'VARIABLE': list(screen[suffix][1:])})
    g.procedure(STARS, body)
    g.insert_after('tic_titleintro_b_00754', [g.call(STARS)])


def _grab(controller, stage):
    g = Graph(controller, stage)
    c = camera_vars(g)
    index = g.var('TitleIntro.i')
    body, world = world_point(g, index)
    work, v = projection(g, [world[a] for a in 'XYZ'], 'TitleIntro.CameraJointProjection', renderer_x=True)
    g.procedure('TITLE | project camera joint', body + work)
    loop = g.blocks['tic_titleintro_b_00038']
    first = loop['inputs']['SUBSTACK'][1]
    call = g.call('TITLE | project camera joint')
    loop['inputs']['SUBSTACK'] = [2, call]
    g.blocks[call].update(parent='tic_titleintro_b_00038', next=first)
    g.blocks[first]['parent'] = call
    for axis in 'XY':
        anchor = 'tic_v22_grab_set' + axis.lower()
        val = g.op('add', 160, g.op('divide', v['ScreenX'], 1.5)) if axis == 'X' else g.op('subtract', 120, g.op('divide', v['ScreenY'], 1.5))
        g.blocks[anchor]['inputs']['ITEM'] = [3, val, [10, '0']]
        g.blocks[val]['parent'] = anchor
    # Selection distance must use the moving eye as well as the hit boxes.
    distance_body, distance_world = world_point(g, index)
    delta = {a: g.var('TitleIntro.CameraJointDistance' + a) for a in 'XYZ'}
    distance_body += [g.set(delta[a], g.op('subtract', distance_world[a], c['Eye' + a])) for a in 'XYZ']
    distance_body += [g.set(g.var('TitleIntro.WorldDist'), g.math('sqrt', dot(g, list(delta.values()), list(delta.values()))))]
    g.procedure('TITLE | camera joint distance', distance_body)
    old = g.blocks['tic_titleintro_b_00253']
    replacement = g.call('TITLE | camera joint distance')
    g.blocks[replacement].update(parent=old['parent'], next=old['next'])
    g.blocks['tic_titleintro_b_00253'] = g.blocks.pop(replacement)
    # Mouse deltas live in camera right/up. Rotate them back to the head's
    # local frame before adding the offsets, preserving the current pose.
    f = g.var('TitleIntro.AnimFrame')
    d = {n: g.var('TitleIntro.CameraDrag' + n) for n in ('Scale', 'DX', 'DY', 'WX', 'WY', 'WZ', 'X2', 'Y1', 'Z1', 'LX', 'LY', 'LZ')}
    mul = lambda a, b: g.op('multiply', a, b)
    trig = lambda fn, a: g.math(fn, g.item('TitleIntro.RootR' + a, f))
    drag = [g.set(d['Scale'], g.op('divide', g.math('sqrt', dot(g, [c['Eye' + a] for a in 'XYZ'], [c['Eye' + a] for a in 'XYZ'])), 1000)),
            g.set(d['DX'], mul(g.op('subtract', g.var('TitleIntro.DragStartX'), g.var('TitleIntro.CursorX')), d['Scale'])),
            g.set(d['DY'], mul(g.op('subtract', g.var('TitleIntro.DragStartY'), g.var('TitleIntro.CursorY')), d['Scale']))]
    drag += [g.set(d['W' + a], g.op('add', mul(d['DX'], c['R' + a]), mul(d['DY'], c['U' + a]))) for a in 'XYZ']
    drag += _inverse_drag_assignments(g, d, trig)
    selected = g.var('TitleIntro.SelectedGrab')
    drag += [g.node('data_replaceitemoflist', {'INDEX': selected, 'ITEM': g.op('add', g.item('TitleIntro.GrabOffset' + a, selected), d['L' + a])},
                    {'LIST': g.list('TitleIntro.GrabOffset' + a)}) for a in 'XYZ']
    g.procedure('TITLE | drag joint in camera plane', drag)
    old = g.blocks['tic_titleintro_b_00321']
    replacement = g.call('TITLE | drag joint in camera plane')
    g.blocks[replacement].update(parent=old['parent'], next='tic_titleintro_b_00224')
    g.blocks['tic_titleintro_b_00321'] = g.blocks.pop(replacement)
    g.blocks['tic_titleintro_b_00224']['parent'] = 'tic_titleintro_b_00321'
    # The superseded Y assignment is no longer connected to the live graph.
    g.blocks['tic_titleintro_b_00330'].update(parent=None, next=None)


def _particles(project, stage):
    for target in project['targets']:
        if target['name'] == 'Title Intro Grab Sparkles':
            g = Graph(target, stage)
            c = camera_vars(g)
            coords = [g.var('GrabSpark.' + a, local=True) for a in 'XYZ']
            work, v = projection(g, coords, 'GrabSpark.Camera', local=True, renderer_x=True)
            # Replace the fixed-eye draw sequence in the existing move
            # procedure; its burst, timeout and facial state remain intact.
            anchor = next(k for k, b in g.blocks.items() if b['opcode'] == 'data_setvariableto'
                          and b['fields'].get('VARIABLE', [''])[0] == 'GrabSpark.Depth')
            following = g.blocks[anchor]['next']
            prev = g.blocks[anchor]['parent']
            first = g.chain(work, prev)
            for b in g.blocks.values():
                if b.get('next') == anchor:
                    b['next'] = first
                for value in b.get('inputs', {}).values():
                    if len(value) > 1 and value[1] == anchor:
                        value[1] = first
            g.blocks[work[-1]]['next'] = following
            g.blocks[following]['parent'] = work[-1]
            g.blocks[anchor].update(parent=None, next=None)
            # Keep original depth reporters pointing at the new result.
            work.append(g.set(g.var('GrabSpark.Depth', local=True), v['Depth']))
            g.blocks[work[-2]]['next'] = work[-1]
            g.blocks[work[-1]].update(parent=work[-2], next=following)
            g.blocks[following]['parent'] = work[-1]
            for b in g.blocks.values():
                if b['opcode'] == 'motion_gotoxy':
                    b['inputs'] = {'X': [3, [12, v['ScreenX'][1], v['ScreenX'][2]], [10, '']],
                                   'Y': [3, [12, v['ScreenY'][1], v['ScreenY'][2]], [10, '']]}
            velocity = next(k for k, b in g.blocks.items() if b['opcode'] == 'data_changevariableby'
                            and b['fields'].get('VARIABLE', [''])[0] == 'GrabSpark.VZ')
            # The former constant +Z kick becomes a camera-facing kick.
            b = g.blocks[velocity]
            kick = [g.set(g.var('GrabSpark.V' + a, local=True), g.op('multiply', c['F' + a], -50), True) for a in 'XYZ']
            before, after = b['parent'], b['next']
            g.blocks[before]['next'] = g.chain(kick, before)
            g.blocks[kick[-1]]['next'] = after
            if after: g.blocks[after]['parent'] = kick[-1]
        elif target['name'] in ('Title Intro Silver Sparkle2', 'Title Intro Red Sparkle2'):
            _trail(target, stage)


def _trail(target, stage):
    colour = 'Silver' if 'Silver' in target['name'] else 'Red'
    target.update(blocks={}, variables={}, visible=False)
    g = Graph(target, stage)
    c = camera_vars(g)
    v = {n: g.var('Trail.' + n, local=True) for n in ('Clone', 'Life', 'X', 'Y', 'Z', 'VX', 'VY', 'VZ', 'Tick')}
    active, tick, frame = (g.var('TitleIntro.' + n) for n in ('Active', 'GlobalTimer', 'AnimFrame'))
    prefix = 'TitleIntro.' + colour + 'Star'
    work, projected = projection(g, [v[a] for a in 'XYZ'], 'Trail.Camera', local=True, renderer_x=True)
    draw = work + [g.control('control_if_else', g.op('and', g.op('gt', projected['Depth'], 30), g.op('lt', projected['Depth'], 5000.00001)), [
        g.node('motion_gotoxy', {'X': projected['ScreenX'], 'Y': projected['ScreenY']}),
        g.node('looks_setsizeto', {'SIZE': g.op('divide', 76800, projected['Depth'])}),
        g.node('looks_switchcostumeto', {'COSTUME': v['Life']}), g.node('looks_show')], [g.node('looks_hide')])]
    move = [g.set(v[a], v['V' + a], True) for a in 'XYZ'] + [g.set(v['VY'], -0.4, True)]
    move += [g.set(v['V' + a], g.op('multiply', v['V' + a], 0.9)) for a in 'XYZ']
    move += [g.set(v['Life'], -1, True), g.control('control_if_else', g.op('gt', v['Life'], 0), draw, [g.node('looks_hide')])]
    g.procedure('TITLE | move world star trail', move)
    random = lambda: g.op('random', -25.0, 25.0)
    spawn = [g.set(v['Life'], g.math('floor', g.op('subtract', 12, g.op('random', 0.0, 5.0))))]
    spawn += [g.set(v[a], g.item(prefix + 'World' + a, frame)) for a in 'XYZ']
    spawn += [g.set(v['V' + a], random()) for a in 'XYZ']
    spawn += [g.control('control_repeat_until', g.op('lt', dot(g, [v['V' + a] for a in 'XYZ'], [v['V' + a] for a in 'XYZ']), 900.000001),
                        [g.set(v['V' + a], random()) for a in 'XYZ'])]
    spawn += [g.set(v['V' + a], g.op('multiply', c['F' + a], 20), True) for a in 'XYZ']
    spawn += [g.call('TITLE | move world star trail')]
    g.procedure('TITLE | emit world star trail', spawn)
    g.procedure('TITLE | initialize world trail pool', [g.control('control_repeat', 30, [g.node('control_create_clone_of', {'CLONE_OPTION': '_myself_'})])])
    loop = [g.node('control_wait_until', {'CONDITION': g.op('or', g.op('equals', active, 0), g.op('gt', tick, v['Tick']))}),
            g.control('control_if', g.op('equals', active, 1), [g.set(v['Tick'], tick),
                g.control('control_if_else', g.op('gt', v['Life'], 0), [g.call('TITLE | move world star trail')], [
                    g.control('control_if', g.op('equals', g.var(prefix + 'ScreenVisible'), 1), [g.call('TITLE | emit world star trail')])])])]
    g.hat('event_whenflagclicked', [g.node('control_delete_this_clone'), g.set(v['Clone'], 0), g.node('looks_hide')])
    g.hat('event_whenbroadcastreceived', [g.control('control_if', g.op('equals', v['Clone'], 0), [g.node('looks_hide'), g.call('TITLE | initialize world trail pool')])],
          ['TITLE INTRO HEAD', 'titleintro_bc_head'])
    g.hat('control_start_as_clone', [g.set(v['Clone'], 1), g.set(v['Life'], 0), g.set(v['Tick'], tick), g.node('looks_hide'),
          g.node('looks_cleargraphiceffects'), g.control('control_repeat_until', g.op('equals', active, 0), loop), g.node('control_delete_this_clone')])
    g.hat('event_whenbroadcastreceived', [g.node('looks_hide'), g.node('control_delete_this_clone')], ['TITLE INTRO END', 'titleintro_bc_end'])
    for costume in target['costumes']:
        costume.update(rotationCenterX=16, rotationCenterY=32, bitmapResolution=1)


def _migrate_grab_renderer_x(project):
    """Fix v1/v2 hit boxes, drag and burst without changing star tracks."""
    for target_name, variable in (
            ('Title Intro Controller', 'TitleIntro.CameraJointProjectionScreenX'),
            ('Title Intro Controller', 'TitleIntro.CameraDragDX'),
            ('Title Intro Grab Sparkles', 'GrabSpark.CameraScreenX')):
        target = next(t for t in project['targets'] if t['name'] == target_name)
        stage = next(t for t in project['targets'] if t.get('isStage'))
        g = Graph(target, stage)
        assignments = [key for key, block in g.blocks.items()
                       if block['opcode'] == 'data_setvariableto'
                       and block['fields'].get('VARIABLE', [''])[0] == variable
                       and isinstance(block['inputs']['VALUE'][1], str)]
        if len(assignments) != 1:
            raise ValueError('Expected one live computed assignment for ' + variable)
        key = assignments[0]
        old = g.blocks[key]['inputs']['VALUE'][1]
        reflected = g.op('multiply', old, -1)
        g.blocks[key]['inputs']['VALUE'] = [3, reflected, [10, '']]
        g.blocks[reflected]['parent'] = key


def _inverse_drag_assignments(g, d, trig):
    mul = lambda a, b: g.op('multiply', a, b)
    inverse_y_x = lambda: g.op('subtract', mul(d['WX'], trig('cos', 'Y')), mul(d['Y1'], trig('sin', 'Y')))
    return [
        g.set(d['X2'], g.op('add', mul(d['WY'], trig('cos', 'X')), mul(d['WZ'], trig('sin', 'X')))),
        g.set(d['Y1'], g.op('subtract', mul(d['WZ'], trig('cos', 'X')), mul(d['WY'], trig('sin', 'X')))),
        g.set(d['LX'], g.op('add', mul(inverse_y_x(), trig('cos', 'Z')), mul(d['X2'], trig('sin', 'Z')))),
        g.set(d['Z1'], inverse_y_x()),
        g.set(d['LY'], g.op('subtract', mul(d['X2'], trig('cos', 'Z')), mul(d['Z1'], trig('sin', 'Z')))),
        g.set(d['LZ'], g.op('add', mul(d['WX'], trig('sin', 'Y')), mul(d['Y1'], trig('cos', 'Y'))))]


def _migrate_root_rotation(project):
    stage = next(t for t in project['targets'] if t.get('isStage'))
    controller = next(t for t in project['targets'] if t['name'] == 'Title Intro Controller')
    g = Graph(controller, stage)
    f = g.var('TitleIntro.AnimFrame')
    # Reuse the existing statement IDs/chain; replace only their expressions.
    for prefix in ('TitleIntro.CameraJoint', 'TitleIntro.Burst'):
        first = len(g.blocks)
        body, _ = world_point(g, g.var('TitleIntro.i'))
        template_keys = list(g.blocks)[first:]
        replacements = {}
        for key in body[3:]:
            block = g.blocks[key]
            name = block['fields']['VARIABLE'][0]
            replacements[name.replace('TitleIntro.CameraJoint', prefix)] = block['inputs']['VALUE']
        if prefix != 'TitleIntro.CameraJoint':
            # Clone work expressions into the burst's own variables.
            for key in template_keys:
                for value in g.blocks[key].get('inputs', {}).values():
                    if len(value) > 1 and isinstance(value[1], list) and value[1][0] == 12:
                        name = value[1][1]
                        if name.startswith('TitleIntro.CameraJoint'):
                            var = g.var(name.replace('TitleIntro.CameraJoint', prefix))
                            value[1] = [12, var[1], var[2]]
        for name, value in replacements.items():
            targets = [key for key, block in list(g.blocks.items())
                       if block['opcode'] == 'data_setvariableto'
                       and block['fields'].get('VARIABLE', [''])[0] == name
                       and key not in body]
            # CameraJoint is evaluated in both projection and distance.
            # Deep-copy reporters for each assignment to keep unique parents.
            import copy
            def clone_input(value, parent):
                value = copy.deepcopy(value)
                if isinstance(value[1], str) and value[1] in g.blocks:
                    old = g.blocks[value[1]]
                    new = g.node(old['opcode'], fields=copy.deepcopy(old['fields']))
                    g.blocks[new]['inputs'] = {k: clone_input(v, new) for k, v in old['inputs'].items()}
                    g.blocks[new]['parent'] = parent
                    value[1] = new
                return value
            for key in targets:
                g.blocks[key]['inputs']['VALUE'] = clone_input(value, key)
        # Generated statements are templates, not executable loose blocks.
        for key in template_keys:
            g.blocks.pop(key, None)
    d = {n: g.var('TitleIntro.CameraDrag' + n) for n in ('WX', 'WY', 'WZ', 'X2', 'Y1', 'Z1', 'LX', 'LY', 'LZ')}
    trig = lambda fn, a: g.math(fn, g.item('TitleIntro.RootR' + a, f))
    body = _inverse_drag_assignments(g, d, trig)
    for template in body:
        name = g.blocks[template]['fields']['VARIABLE'][0]
        key = next(key for key, block in g.blocks.items() if key not in body
                   and block['opcode'] == 'data_setvariableto'
                   and block['fields'].get('VARIABLE', [''])[0] == name)
        value = g.blocks[template]['inputs']['VALUE']
        g.blocks[key]['inputs']['VALUE'] = value
        g.blocks[value[1]]['parent'] = key
        g.blocks.pop(template)


def apply_camera_controls(project):
    metadata = project.get('extensionData', {})
    if metadata.get('intro_only_base'):
        if metadata.get('v13_goddard_camera', {}).get('version') != 5:
            raise ValueError('Regenerate the intro base from an updated BASE_FULL before patching.')
        from prepare_intro_base import validate_graph
        validate_graph(project)
        return metadata['v13_goddard_camera']
    controller = next(t for t in project['targets'] if t['name'] == 'Title Intro Controller')
    if MARKER in controller['blocks']:
        # v1 accidentally projected once after the seven-joint loop. Move
        # that call into the loop so each hit box uses its own current joint.
        b = controller['blocks']
        loop = b['tic_titleintro_b_00038']
        call = loop['next']
        if (call in b and b[call]['opcode'] == 'procedures_call'
                and b[call]['mutation']['proccode'] == 'TITLE | project camera joint'):
            following = b[call]['next']
            loop['next'] = following
            if following: b[following]['parent'] = 'tic_titleintro_b_00038'
            first = loop['inputs']['SUBSTACK'][1]
            loop['inputs']['SUBSTACK'] = [2, call]
            b[call].update(parent='tic_titleintro_b_00038', next=first)
            b[first]['parent'] = call
        result = project.setdefault('extensionData', {}).setdefault('v13_goddard_camera', {})
        if result.get('version', 1) < 3:
            _migrate_grab_renderer_x(project)
            _migrate_root_rotation(project)
        if result.get('version', 1) < 4:
            # Stars/trails are billboards in the same renderer convention as
            # the mesh. v1-v3 omitted Render's final X reflection here too.
            for name, variables in [('Title Intro Controller', ['TitleIntro.SilverStarCameraScreenX', 'TitleIntro.RedStarCameraScreenX']),
                                    ('Title Intro Silver Sparkle2', ['Trail.CameraScreenX']),
                                    ('Title Intro Red Sparkle2', ['Trail.CameraScreenX'])]:
                target = next(t for t in project['targets'] if t['name'] == name)
                stage = next(t for t in project['targets'] if t.get('isStage'))
                g = Graph(target, stage)
                for key, block in list(g.blocks.items()):
                    if block['opcode'] == 'data_setvariableto' and block['fields'].get('VARIABLE', [''])[0] in variables:
                        value = block['inputs']['VALUE'][1]
                        if isinstance(value, str):
                            reflected = g.op('multiply', value, -1)
                            block['inputs']['VALUE'] = [3, reflected, [10, '']]
                            g.blocks[reflected]['parent'] = key
        if result.get('version', 1) < 5:
            stage = next(t for t in project['targets'] if t.get('isStage'))
            g = Graph(controller, stage)
            for key, block in list(g.blocks.items()):
                name = block.get('fields', {}).get('VARIABLE', [''])[0]
                if block['opcode'] == 'data_setvariableto' and name in (
                        'TitleIntro.SilverStarScreenSize', 'TitleIntro.RedStarScreenSize'):
                    old = block['inputs']['VALUE'][1]
                    if isinstance(old, str):
                        colour = 'Silver' if 'Silver' in name else 'Red'
                        val = g.op('divide', 25600, g.var('TitleIntro.' + colour + 'StarCameraDepth'))
                        block['inputs']['VALUE'] = [3, val, [10, '']]
                        g.blocks[val]['parent'] = key
                if block['opcode'] == 'operator_gt':
                    a = block['inputs'].get('OPERAND1', [None, None])[1]
                    val = block['inputs'].get('OPERAND2', [None, None])[1]
                    if isinstance(a, list) and a[0] == 12 and 'StarCameraDepth' in a[1] and isinstance(val, list) and str(val[1]) == '256':
                        val[1] = '30'
        for target in project['targets']:
            if target['name'] in ('Title Intro Silver Star', 'Title Intro Red Star'):
                target['blocks']['lt661']['inputs']['OPERAND2'] = [1, [4, '821']]
        result.update(version=5, star_horizontal='renderer_final_x_reflection', grab_projection='once_per_joint_inside_seven_joint_loop',
                      grab_horizontal='renderer_final_x_reflection_and_inverse_drag',
                      grab_root_rotation='renderer_qX_qY_qZ_forward_and_inverse')
        return result
    stage = next(t for t in project['targets'] if t.get('isStage'))
    _controls(controller, stage)
    _render_basis(project, stage)
    _stars(controller, project, stage)
    _grab(controller, stage)
    _particles(project, stage)
    result = dict(version=5, star_horizontal='renderer_final_x_reflection', grab_projection='once_per_joint_inside_seven_joint_loop',
                  grab_horizontal='renderer_final_x_reflection_and_inverse_drag', arrows='US C buttons; 4 degrees per 30Hz tick; pitch +/-80',
                  grab_root_rotation='renderer_qX_qY_qZ_forward_and_inverse',
                  x='B zoom cycle: 1, 1.5, 2; edge triggered', eye_smoothing=0.25,
                  projection='shared renderer basis for head, stars, trails and grabbers',
                  sounds=['0x70060080', '0x70070080'])
    project.setdefault('extensionData', {})['v13_goddard_camera'] = result
    return result
