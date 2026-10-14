"""Reset collected-star flags and initialize the death-return camera locally."""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition, _call


def apply_session_death_camera(project):
    meta = project.setdefault('extensionData', {})
    targets = {t['name']: t for t in project['targets']}
    stage = next(t for t in project['targets'] if t.get('isStage'))
    world = targets['World + Assets']
    marker = 'session_death_camera_v19_reset'
    if marker in targets['Main Loop']['blocks']:
        return meta['session_death_camera_v19']

    # A flag press starts a fresh session, whereas room/course reloads retain
    # collected stars. The old initializer reset BOB1 but omitted BOB6.
    collected = sorted(row[0] for row in stage['variables'].values()
                       if row[0].startswith('Star.') and row[0].endswith('Collected'))
    names = ['Star.Count', *collected, 'Star.State', 'Star.Returning',
             'Star.ModelDirty', 'Chomp.StarCollecting']
    for target_name, anchor in [('Main Loop', '/U'),
                                ('World + Assets', None)]:
        g = Graph(targets[target_name], stage)
        body = [g.set(g.var(name), 0) for name in names]
        if target_name == 'Main Loop':
            first = body[0]
            g.blocks[marker] = g.blocks.pop(first)
            body[0] = marker
        g.insert_after(anchor or _definition(g, 'WORLD | initialize'), body)
    for row in stage['variables'].values():
        if row[0] in names:
            row[1] = 0

    wg = Graph(world, stage)
    camera_reset_names = ('CamCastle.DoorTail', 'RoomDoor.CameraFollowStarted',
        'CamSM64.Init', 'CamSM64.PendingSnap', 'CamSM64.TransitionInit',
        'CamSM64.TransitionFrames', 'CamSM64.SnapLakitu', 'CamSM64.PostDoor',
        'CamSM64.PostDoorWas', 'Intro.HandoffPending')
    wg.insert_after('d133w_00003', [
        *[wg.set(wg.var(name), 0) for name in camera_reset_names],
        wg.set(wg.var('CamCastle.Mode'), 'CLOSE')])

    # The first lobby scene is rendered in the world phase, after this frame's
    # Mario camera phase. Initialize orientation here, before revealing it.
    dx = wg.var('Camera64.DX'); dy = wg.var('Camera64.DY'); dz = wg.var('Camera64.DZ')
    horizontal = wg.var('Camera64.Horizontal')
    camera = [
        wg.set(dx, wg.op('subtract', wg.var('Camera64.FocusX'), wg.var('cam x'))),
        wg.set(dy, wg.op('subtract', wg.var('Camera64.FocusY'), wg.var('cam y'))),
        wg.set(dz, wg.op('subtract', wg.var('Camera64.FocusZ'), wg.var('cam z'))),
        wg.set(horizontal, wg.math('sqrt', wg.op('add',
            wg.op('multiply', dx, dx), wg.op('multiply', dz, dz)))),
        wg.control('control_if', wg.op('lt', horizontal, .001),
                   [wg.set(horizontal, .001)]),
        _call(wg, 'WORLD MATH | atan2 y %s x %s', [dx, dz]),
        wg.set(wg.var('cam ry'), wg.var('Math.Result')),
        wg.set(wg.var('cam rx'), wg.math('atan', wg.op('divide', dy, horizontal))),
        wg.set(wg.var('Camera64.Yaw'),
               wg.op('mod', wg.op('add', wg.var('cam ry'), 180), 360)),
        wg.set(wg.var('Camera64.Distance'), horizontal),
        wg.set(wg.var('Camera64.DistanceGoal'), horizontal),
        wg.set(wg.var('cam rz'), 0)]
    for axis in 'XYZ':
        position = wg.var('cam ' + axis.lower())
        focus = wg.var('Camera64.Focus' + axis)
        for name in ('Camera64.Goal', 'CamSM64.Goal', 'CamSM64.PrevGoal', 'CamSM64.Trans'):
            camera.append(wg.set(wg.var(name + axis), position))
        for name in ('Camera64.GoalFocus', 'CamSM64.GoalFocus', 'CamSM64.TransF'):
            camera.append(wg.set(wg.var(name + axis), focus))
    wg.procedure('WORLD | prepare death return camera', camera)
    wg.insert_after('d133w_00074', [wg.call('WORLD | prepare death return camera')])

    # Seed the normal close camera from the final death shot on handoff. A
    # previously initialized BOB controller must not reuse its old goals.
    mg = Graph(targets['Mario'], stage)
    mg.insert_after('d133m_00079', [
        *[mg.set(mg.var(name), 0) for name in camera_reset_names],
        mg.set(mg.var('CamCastle.Mode'), 'CLOSE')])
    report = dict(version=1, reset_collected_flags=collected,
        reset_scope='Green flag and world initialization; ordinary course reloads preserve progress',
        death_return='Initialize orientation and goals before first lobby render; clear previous camera controller at entry and handoff',
        runtime_verified=False)
    meta['session_death_camera_v19'] = report
    return report
