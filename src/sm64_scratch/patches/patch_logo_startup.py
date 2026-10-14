"""Prepare title resources before its audio and omit unused sky sampling."""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition
from sm64_scratch.render_targets import intro_render_target


def apply_logo_startup(project):
    stage = next(t for t in project['targets'] if t.get('isStage'))
    controller = next(t for t in project['targets']
                      if t['name'] == 'Title Intro Controller')
    render = intro_render_target(project)
    meta = project.setdefault('extensionData', {})
    marker = 'logo_startup_v17_present'
    if marker in render['blocks']:
        return meta['logo_startup_v17']

    # The early notification started audio while initializing 43,200 pixels
    # and parsing the logo model. Begin the logo only once that work is done.
    cg = Graph(controller, stage)
    event = 'tic_titleintro_b_00678'
    previous = cg.blocks[event]['parent']
    following = cg.blocks[event]['next']
    cg.blocks[previous]['next'] = following
    cg.blocks[following]['parent'] = previous
    anchor = 'tic_titleintro_b_00693'  # final model/queue preparation
    following = cg.blocks[anchor]['next']
    cg.blocks[anchor]['next'] = event
    cg.blocks[event].update(parent=anchor, next=following)
    cg.blocks[following]['parent'] = event

    rg = Graph(render, stage)
    # Apply the gameplay clipping repair to the isolated intro renderer too.
    # An offscreen face may not abort the caller's entire frame in Scratch.
    guard = rg.blocks['cP']
    if 'ad)' in rg.blocks:
        remaining = guard['next']
        condition = rg.op('not', guard['inputs']['CONDITION'][1])
        guard['inputs'] = {'CONDITION': [2, condition],
                           'SUBSTACK': [2, remaining]}
        rg.blocks[condition]['parent'] = 'cP'
        rg.blocks[remaining]['parent'] = 'cP'
        guard['next'] = None
        rg.blocks.pop('ad)')

    # The logo has a black background. Its presenter formerly calculated sky
    # coordinates each row and advanced/wrapped them for every output pixel.
    # Retain identical horizontal pen runs and depth/texture buffer clearing.
    idx, x = rg.var('idx1'), rg.var('x')
    color, last = rg.var('Sky.Color'), rg.var('last color')
    res = rg.var('res')
    def read_color():
        return rg.control('control_if_else',
            rg.op('equals', rg.item('tex buffer', idx), 16777216),
            [rg.set(color, 0)], [rg.set(color, rg.item('tex buffer', idx))])
    pixels = [read_color(),
        rg.control('control_if', rg.op('not', rg.op('equals', color, last)), [
            rg.node('pen_setPenColorToColor', {'COLOR': last}),
            rg.node('motion_setx', {'X': x}), rg.set(last, color)]),
        rg.node('data_replaceitemoflist', {'INDEX': idx, 'ITEM': 0},
                {'LIST': rg.list('depth buffer')}),
        rg.node('data_replaceitemoflist', {'INDEX': idx, 'ITEM': 16777216},
                {'LIST': rg.list('tex buffer')}),
        rg.set(idx, 1, True), rg.set(x, res, True)]
    rows = [rg.node('motion_setx', {'X': -240}), rg.set(x, -240),
        read_color(), rg.set(last, color),
        rg.node('pen_setPenColorToColor', {'COLOR': last}), rg.node('pen_penDown'),
        rg.control('control_repeat', rg.var('screen width'), pixels),
        rg.node('pen_setPenColorToColor', {'COLOR': last}),
        rg.node('motion_setx', {'X': x}), rg.node('pen_penUp'),
        rg.node('motion_changeyby', {'DY': res})]
    rg.procedure('TITLE | present logo on black background', [
        rg.node('pen_clear'), rg.node('pen_setPenSizeTo', {'SIZE': res}),
        rg.set(idx, 1), rg.node('motion_sety', {'Y': -180}),
        rg.control('control_repeat', rg.var('screen height'), rows)])
    definition = _definition(rg, 'RENDER | present')
    original = rg.blocks[definition]['next']
    wrapper = rg.control('control_if_else', rg.op('and',
        rg.op('equals', rg.var('TitleIntro.Active'), 1),
        rg.op('equals', rg.var('TitleIntro.Phase'), 1)),
        [rg.call('TITLE | present logo on black background')])
    node = rg.blocks.pop(wrapper)
    for value in node['inputs'].values():
        if isinstance(value[1], str):
            rg.blocks[value[1]]['parent'] = marker
    node['inputs']['SUBSTACK2'] = [2, original]
    node.update(parent=definition)
    rg.blocks[original]['parent'] = marker
    rg.blocks[definition]['next'] = marker
    rg.blocks[marker] = node
    report = dict(version=1,
        start_event='after_render_buffers_and_logo_model_are_ready',
        presenter='black_background_without_unused_sky_sampling',
        offscreen_faces='skip_face_without_stopping_frame',
        animation_samples=91, animation_fps=30,
        runtime_performance_measured=False)
    meta['logo_startup_v17'] = report
    return report
