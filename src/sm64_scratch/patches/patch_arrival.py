"""Buffered title input and an atomic, visible garden arrival.

All additions are Scratch logic and empty state. ROM camera samples and media
remain constructor inputs. The existing v0.85 gameplay camera owns the final
part of the cinematic, so releasing input does not start a second camera move.
"""
import copy
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _call


def _clone_chain(g, first):
    mapping = {}
    def clone(key):
        if key in mapping:
            return mapping[key]
        old = g.blocks[key]
        new = g.node(old['opcode'])
        mapping[key] = new
        value = copy.deepcopy(old)
        value.update(parent=None, topLevel=False)
        for slot, inp in value['inputs'].items():
            for i in range(1, len(inp)):
                if isinstance(inp[i], str) and inp[i] in g.blocks:
                    inp[i] = clone(inp[i])
                    g.blocks[inp[i]]['parent'] = new
        value['next'] = clone(old['next']) if old.get('next') else None
        if value['next']:
            g.blocks[value['next']]['parent'] = new
        g.blocks[new] = value
        return new
    return clone(first)


def _ensure_startup_render_queue(project):
    """Build the visible object queue before rendering/revealing the garden.

    WORLD initialization loads models and creates Scene records, but leaves
    Render Queue empty. The asset update populates that queue synchronously;
    no simulation tick or repeated loading loop is required here.
    """
    stage = next(t for t in project['targets'] if t['isStage'])
    main = next(t for t in project['targets'] if t['name'] == 'Main Loop')
    g = Graph(main, stage)
    if 'arrival_first_frame' not in g.blocks:
        return
    anchor = 'arrival_first_frame'
    while anchor:
        block = g.blocks[anchor]
        label = block.get('inputs', {}).get('BROADCAST_INPUT', [None, None])[1]
        if isinstance(label, list) and len(label) > 1:
            if label[1] == 'MODULE ASSETS UPDATE':
                return
            if label[1] == 'MODULE WORLD SYNC':
                following = g.blocks.get(block.get('next'), {})
                next_input = following.get('inputs', {}).get('BROADCAST_INPUT', [None, None])[1]
                if (isinstance(next_input, list) and len(next_input) > 1
                        and next_input[1] == 'MODULE ASSETS UPDATE'):
                    return
                identity = next(k for k, v in stage['broadcasts'].items()
                                if v == 'MODULE ASSETS UPDATE')
                key = g.node('event_broadcastandwait')
                g.blocks[key]['inputs'] = {
                    'BROADCAST_INPUT': [1, [11, 'MODULE ASSETS UPDATE', identity]]}
                g.insert_after(anchor, [key])
                return
            if label[1] == 'TITLE INTRO GAMEPLAY FADE IN':
                break
        anchor = block.get('next')
    raise ValueError('Garden startup world-sync barrier is missing')


def _center_first_idle_after_pipe(project):
    """Enter the first post-pipe idle on the native center -> left transition.

    ``SM64 STATE | set action`` already resets ``SM64.ActionState`` to 0,
    which is the native ``MARIO_ANIM_IDLE_HEAD_LEFT`` state.  That clip starts
    from the centered pose and turns the head left.  The Scratch idle graph,
    however, checks ``Anim.End`` before requesting the new idle clip, so the
    end flag from the pipe/landing animation can advance the state immediately
    and make the first visible idle pose jump to the left.

    Clear that stale end flag and explicitly retain state 0 on the first idle
    tick after the arrival handoff.  The normal 0 -> 1 -> 2 idle sequence then
    runs unchanged (center->left, left->right, right->center).
    """
    stage = next(t for t in project['targets'] if t['isStage'])
    mario = next(t for t in project['targets'] if t['name'] == 'Mario')
    g = Graph(mario, stage)
    marker = 'arrival_pipe_forward_idle'
    if marker in g.blocks:
        return
    definition = next(k for k, b in g.blocks.items()
                      if b['opcode'] == 'procedures_definition'
                      and g.blocks[b['inputs']['custom_block'][1]]['mutation']['proccode']
                      == 'SM64 ACT | idle')
    centered = g.var('Intro.ArrivalIdleCentered', 0)
    # Trigger on the action handoff itself, not on the camera endpoint.
    # The fixed-tick loop executes Mario's action before it advances the
    # arrival camera / clears Intro.Active, so waiting for the final camera
    # sample makes this correction happen one tick too late: controls are
    # already live.  PrevAction identifies the exact pipe landing -> idle
    # handoff while Intro.Active keeps this scoped to the arrival cinematic.
    ready = g.op('and',
                 g.op('and', g.op('equals', g.var('Arrival.Ready'), 1),
                      g.op('equals', g.var('Intro.Active'), 1)),
                 g.op('equals', g.var('SM64.PrevAction'), 'ACT_JUMP_LAND_STOP'))
    def variable_name(ref):
        if isinstance(ref, str) and ref in g.blocks:
            return g.blocks[ref].get('fields', {}).get('VARIABLE', [None])[0]
        if isinstance(ref, list) and len(ref) > 1:
            if ref[0] == 12:
                return ref[1]
        return None
    def literal_value(ref):
        if isinstance(ref, list) and len(ref) > 1:
            if ref[0] in (4, 10, 11):
                return str(ref[1])
        return None
    first_idle = g.op('and', g.op('equals', g.var('SM64.Action'), 'ACT_IDLE'),
                      g.op('equals', centered, 0))
    pose = g.control('control_if', g.op('and', ready, first_idle), [
        # Native set_mario_action(ACT_IDLE, 0) starts at state 0.  Do not
        # synthesize state 2 here: state 0's clip itself begins centered.
        g.set(g.var('SM64.ActionState'), 0),
        # The Scratch port tests Anim.End before requesting the state's clip.
        # Discard the previous action's terminal flag so it cannot skip state 0.
        g.set(g.var('Anim.End'), 0),
        g.set(centered, 1)])
    g.blocks[marker] = g.blocks.pop(pose)
    g.insert_after(definition, [marker])
    g.hat('event_whenflagclicked', [g.set(centered, 0)])
    project.setdefault('extensionData', {})['pipe_forward_idle_fix'] = {
        'version': 5,
        'trigger': 'First ACT_IDLE immediately after ACT_JUMP_LAND_STOP while the arrival intro is still active',
        'idle_pose': 'Preserve native state 0: centered first frame, then head-left, head-right, head-center',
        'action_state_sequence': 'state 0 retained; stale Anim.End cleared on the landing-to-idle handoff before control release',
        'decomp_alignment': 'set_mario_action resets actionState to 0; act_idle state 0 requests MARIO_ANIM_IDLE_HEAD_LEFT',
    }


def apply_arrival_fixes(project):
    metadata = project.setdefault('extensionData', {})
    targets = {t['name']: t for t in project['targets']}
    main, controller = targets['Main Loop'], targets['Title Intro Controller']
    if (metadata.get('garden_arrival_fix', {}).get('version') == 1
            and 'arrival_first_frame' in main['blocks']
            and any(b.get('mutation', {}).get('proccode') == 'INTRO | finish camera before control'
                    for b in targets['Mario']['blocks'].values())
            and any(b.get('fields', {}).get('KEY_OPTION', [None])[0] == 'space'
                    for b in controller['blocks'].values() if b['opcode'] == 'event_whenkeypressed')):
        _ensure_startup_render_queue(project)
        metadata['garden_arrival_fix']['startup_queue_revision'] = 2
        metadata['garden_arrival_fix']['white_reveal'] = (
            'camera, scene sync, asset/render queue update and first render complete before fade-in')
        _center_first_idle_after_pipe(project)
        return metadata['garden_arrival_fix']
    stage = next(t for t in project['targets'] if t['isStage'])
    g = Graph(controller, stage)
    requested = g.var('TitleIntro.StartRequested')
    ready = g.var('Arrival.Ready')
    if not any(b['opcode'] == 'event_whenkeypressed' and b.get('fields', {}).get('KEY_OPTION', [None])[0] == 'space' for b in g.blocks.values()):
        # Reset before the logo; a quick down/up remains pending during head entry.
        definition = next(k for k, b in g.blocks.items() if b['opcode'] == 'procedures_definition'
                          and g.blocks[b['inputs']['custom_block'][1]]['mutation']['proccode']
                          == 'TITLE | run original 3D intro')
        g.insert_after(definition, [g.set(requested, 0), g.set(ready, 0)])
        for key in ('space', 'enter'):
            hat = g.node('event_whenkeypressed', fields={'KEY_OPTION': [key, None]})
            g.blocks[hat].update(topLevel=True, x=0, y=4000)
            body = g.control('control_if', g.op('and', g.op('equals', g.var('TitleIntro.Active'), 1),
                             g.op('equals', g.var('TitleIntro.Phase'), 2)), [g.set(requested, 1)])
            g.blocks[hat]['next'] = g.chain([body], hat)
        condition = g.op('and', g.op('gt', g.var('TitleIntro.Frame'), 20),
                         g.op('equals', requested, 1))
        g.blocks['tic_titleintro_b_00753']['inputs']['CONDITION'] = [2, condition]
        g.blocks[condition]['parent'] = 'tic_titleintro_b_00753'

    g = Graph(main, stage)
    def broadcast(name, wait=True):
        identity = next(k for k, v in stage['broadcasts'].items() if v == name)
        key = g.node('event_broadcastandwait' if wait else 'event_broadcast')
        g.blocks[key]['inputs'] = {'BROADCAST_INPUT': [1, [11, name, identity]]}
        return key
    if 'arrival_first_frame' not in g.blocks:
        first = broadcast('MODULE CAMERA UPDATE')
        g.blocks['arrival_first_frame'] = g.blocks.pop(first)
        g.insert_after('CG', ['arrival_first_frame', broadcast('MODULE WORLD SYNC'),
                             broadcast('MODULE RENDER FRAME')])
        # Keep simulation stopped until the already-rendered garden is revealed.
        g.blocks['v23_gameplay_fade_in_broadcast']['opcode'] = 'event_broadcastandwait'
        g.insert_after('v23_gameplay_fade_in_broadcast', [g.set(ready, 1), g.call('ENGINE | run')])

    # No permanent 120 Hz mask keeper. The fade owns its layer while showing;
    # the Main Loop barrier guarantees the next scene exists before revealing.
    fade = targets['Title Intro Gameplay Fade']
    fg = Graph(fade, stage)
    for key, block in list(fg.blocks.items()):
        if block['opcode'] == 'event_whenflagclicked':
            fg.blocks[key]['next'] = fg.chain([fg.node('looks_hide')], key)
    # Unreachable old loops are removed below, as are detached old conditions.

    mario = targets['Mario']
    g = Graph(mario, stage)
    if not any(b.get('mutation', {}).get('proccode') == 'INTRO | finish camera before control' for b in g.blocks.values()):
        rise = g.var('Intro.RiseFrame')
        seeded = g.var('Intro.GameplayCameraReady')
        g.insert_after('+f', [g.set(rise, 0), g.set(seeded, 0)])
        g.blocks['ZU']['inputs']['VALUE'] = [1, [4, -2]]
        g.blocks['ZW']['inputs']['VALUE'] = [1, [4, 1]]
        expr = g.op('divide', rise, 50)
        g.blocks['ultimate_b_48000009']['inputs']['VALUE'] = [3, expr, [10, '']]
        g.blocks[expr]['parent'] = 'ultimate_b_48000009'
        # Hold the first native camera pose for the additional visible rise.
        advance = g.control('control_if_else', g.op('lt', rise, 50),
                            [g.set(rise, 1, True)], [g.set(g.var('Intro.Frame'), 1, True)])
        g.blocks['fJ']['inputs']['SUBSTACK'] = [2, advance]
        g.blocks[advance]['parent'] = 'fJ'

        axes = ['cam x', 'cam y', 'cam z', 'Camera64.FocusX', 'Camera64.FocusY', 'Camera64.FocusZ']
        cache = [g.var('Intro.GameplayCamera' + str(i)) for i in range(6)]
        native = [g.var('Intro.NativeCamera' + str(i)) for i in range(6)]
        ratio = g.var('Intro.CameraBlend')
        # Reuse the engine's exact angle conversion for the blended pose.
        angles = _clone_chain(g, 've')
        g.procedure('INTRO | aim arrival camera', [])
        definition = next(k for k, b in g.blocks.items() if b['opcode'] == 'procedures_definition'
                          and g.blocks[b['inputs']['custom_block'][1]]['mutation']['proccode']
                          == 'INTRO | aim arrival camera')
        g.blocks[definition]['next'] = angles
        g.blocks[angles]['parent'] = definition
        initialization = []
        for i, name in enumerate(axes):
            channel = ('Intro.Camera.' if i < 3 else 'Intro.Focus.') + 'XYZ'[i % 3]
            initialization.append(g.set(g.var(name), g.item(channel, g.length('Intro.Camera.X'))))
        initialization += [g.call('INTRO | aim arrival camera'),
                           g.call('SM64 CAMERA DECOMP2 | init BOB camera'),
                           g.set(g.var('CamSM64.SnapLakitu'), 0),
                           g.set(g.var('CamSM64.PendingSnap'), 0),
                           g.set(g.var('CamSM64.TransitionFrames'), 0),
                           g.set(seeded, 1), g.set(g.var('Intro.CameraInherited'), 1),
                           g.set(g.var('Camera64.JustInherited'), 0)]
        initialization += [g.set(cache[i], g.var(name)) for i, name in enumerate(axes)]
        end = lambda: g.op('subtract', g.length('Intro.Camera.X'), 1)
        start = lambda: g.op('subtract', end(), 70)
        blend = [g.set(native[i], g.var(name)) for i, name in enumerate(axes)]
        blend += [g.control('control_if', g.op('equals', seeded, 0), initialization)]
        blend += [g.set(g.var(name), cache[i]) for i, name in enumerate(axes)]
        blend += [g.call('SM64 CAMERA DECOMP2 | update')]
        blend += [g.set(cache[i], g.var(name)) for i, name in enumerate(axes)]
        blend += [g.set(ratio, g.op('divide', g.op('subtract', g.var('Intro.Frame'), start()), 40)),
                  g.control('control_if', g.op('gt', ratio, 1), [g.set(ratio, 1)]),
                  g.set(ratio, g.op('multiply', g.op('multiply', ratio, ratio),
                                   g.op('subtract', 3, g.op('multiply', 2, ratio))))]
        blend += [g.set(g.var(name), g.op('add', native[i],
                  g.op('multiply', g.op('subtract', cache[i], native[i]), ratio)))
                  for i, name in enumerate(axes)]
        g.procedure('INTRO | finish camera before control', [
            g.control('control_if', g.op('gt', g.var('Intro.Frame'), start()), blend)])
        g.insert_after('en', [g.call('INTRO | finish camera before control')])
        # The regular controller has already settled for 30 ticks at release.
        g.insert_after('amQ', [g.set(g.var('Intro.HandoffPending'), 0)])
        old_pending = g.blocks['amQ']['next']
        # The v4 pending=1 assignment follows our new pending=0 assignment.
        following = g.blocks[old_pending]['next']
        if following and g.blocks[following]['fields'].get('VARIABLE', [None])[0] == 'Intro.HandoffPending':
            g.blocks[following]['inputs']['VALUE'] = [1, [4, 0]]

    # Existing audio observer: rise cue must accompany visible rise, not loading.
    audio = targets.get('Gameplay Audio')
    if audio:
        ag = Graph(audio, stage)
        # Locate the single rise-once condition by its guarded latch assignment.
        for key, block in list(ag.blocks.items()):
            if block['opcode'] != 'control_if':
                continue
            child = block.get('inputs', {}).get('SUBSTACK', [None, None])[1]
            if child not in ag.blocks:
                continue
            chain = []
            while child:
                chain.append(ag.blocks[child]); child = ag.blocks[child].get('next')
            if any(b.get('fields', {}).get('VARIABLE', [None])[0] == 'Audio.PipeRisePlayed' for b in chain):
                old = block['inputs']['CONDITION'][1]
                condition = ag.op('and', old, ag.op('equals', ag.var('Arrival.Ready'), 1))
                block['inputs']['CONDITION'] = [2, condition]
                ag.blocks[condition]['parent'] = key
    _center_first_idle_after_pipe(project)
    # Remove only unreachable graph fragments; no reachable source procedures.
    # Other constructors still inspect several disconnected historical anchors;
    # retain those source blocks until that migration contract is updated.
    for target in (fade,):
        blocks = target['blocks']; live = set()
        def visit(key):
            if not isinstance(key, str) or key not in blocks or key in live:
                return
            live.add(key); b = blocks[key]
            visit(b.get('next'))
            for inp in b.get('inputs', {}).values():
                for value in inp[1:]:
                    visit(value)
        for key, block in blocks.items():
            if block.get('topLevel'):
                visit(key)
        target['blocks'] = {k: b for k, b in blocks.items() if k in live}
    result = dict(version=1, title_input='space/enter keydown latch during head entry',
                  white_reveal='first garden render completed before fade-in; simulation starts afterward',
                  visible_pipe_rise_ticks=50, camera_blend_frames_before_end=[70, 30],
                  camera_handoff='existing gameplay controller warmed before control release')
    metadata['garden_arrival_fix'] = result
    _ensure_startup_render_queue(project)
    result['startup_queue_revision'] = 2
    result['white_reveal'] = (
        'camera, scene sync, asset/render queue update and first render complete before fade-in')
    return result
