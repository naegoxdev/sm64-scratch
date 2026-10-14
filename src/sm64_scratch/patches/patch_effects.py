"""Assetless title billboard placement and ROM PRESS START presentation.

Scratch fences sprites at the stage boundary. Four transparent positioning
costumes allow a quad to be placed at its true projected coordinate before
its visual costume is selected, atomically inside a warp procedure.
"""
import copy
from sm64_scratch.render_targets import intro_render_target
from sm64_scratch.patches.patch_animation import Graph

ANCHORS = [('__projection_pp', 735, -15), ('__projection_pn', 735, 555),
           ('__projection_np', -15, -15), ('__projection_nn', -15, 555)]
BILLBOARDS = ['Title Intro Silver Star', 'Title Intro Red Star',
              'Title Intro Silver Sparkle2', 'Title Intro Red Sparkle2',
              'Title Intro Grab Sparkles']
MARKER = 'v13_effect_billboard_draw'
DRAW = 'TITLE | draw projected billboard without edge fencing'


def apply_effect_presentation(project):
    if project.get('extensionData', {}).get('intro_only_base'):
        from prepare_intro_base import validate_graph
        validate_graph(project)
        render = intro_render_target(project)
        if STAR_RASTER_MARKER not in render['blocks']:
            raise ValueError('Regenerate the intro base from an updated BASE_FULL before patching.')
        return project.get('extensionData', {}).get('v13_title_effect_presentation', {})
    stage = next(t for t in project['targets'] if t.get('isStage'))
    for name in BILLBOARDS:
        target = next(t for t in project['targets'] if t['name'] == name)
        if MARKER in target['blocks']:
            continue
        for anchor, x, y in ANCHORS:
            costume = copy.deepcopy(target['costumes'][0])
            costume.update(name=anchor, rotationCenterX=x, rotationCenterY=y,
                           bitmapResolution=1)
            target['costumes'].append(costume)
        target.update(direction=90, rotationStyle="don't rotate")
        g = Graph(target, stage)
        # Retain the exact existing projection, scale and texture-frame
        # reporters. Only the final sprite placement sequence changes.
        goto = next(k for k, b in g.blocks.items() if b['opcode'] == 'motion_gotoxy')
        size = g.blocks[goto]['next']
        costume = g.blocks[size]['next']
        show = g.blocks[costume]['next']
        extras = []
        while g.blocks[show]['opcode'] != 'looks_show':
            extras.append(show)
            show = g.blocks[show]['next']
        assert g.blocks[size]['opcode'] == 'looks_setsizeto'
        assert g.blocks[costume]['opcode'] == 'looks_switchcostumeto'
        assert g.blocks[show]['opcode'] == 'looks_show'
        v = {n: g.var('Billboard.' + n, local=True) for n in ('X', 'Y', 'Size', 'Frame', 'Width')}
        values = {'X': g.blocks[goto]['inputs']['X'], 'Y': g.blocks[goto]['inputs']['Y'],
                  'Size': g.blocks[size]['inputs']['SIZE'], 'Frame': g.blocks[costume]['inputs']['COSTUME']}
        setup = []
        for n, value in values.items():
            key = g.set(v[n], 0)
            g.blocks[key]['inputs']['VALUE'] = copy.deepcopy(value)
            if isinstance(value[1], str) and value[1] in g.blocks:
                g.blocks[value[1]]['parent'] = key
            setup.append(key)
        pixels = 128 if name.endswith('Star') else 32
        # Match Scratch's own size limits for the real visual costume.
        low, high = 500 / pixels, 54000 / pixels
        setup += [g.control('control_if', g.op('lt', v['Size'], low), [g.set(v['Size'], low)]),
                  g.control('control_if', g.op('gt', v['Size'], high), [g.set(v['Size'], high)]),
                  g.set(v['Width'], g.op('multiply', v['Size'], pixels / 100))]
        switch = lambda n: g.node('looks_switchcostumeto', {'COSTUME': n})
        pick_anchor = g.control('control_if_else', g.op('gt', v['X'], -0.000001), [
            g.control('control_if_else', g.op('gt', v['Y'], -0.000001), [switch(ANCHORS[0][0])], [switch(ANCHORS[1][0])])], [
            g.control('control_if_else', g.op('gt', v['Y'], -0.000001), [switch(ANCHORS[2][0])], [switch(ANCHORS[3][0])])])
        half = lambda: g.op('divide', v['Width'], 2)
        intersects = g.op('and',
            g.op('and', g.op('gt', g.op('add', v['X'], half()), -240),
                        g.op('lt', g.op('subtract', v['X'], half()), 240)),
            g.op('and', g.op('gt', g.op('add', v['Y'], v['Width']), -180),
                        g.op('lt', v['Y'], 180)))
        draw = [pick_anchor, g.node('looks_setsizeto', {'SIZE': 100}),
                g.node('motion_gotoxy', {'X': v['X'], 'Y': v['Y']}),
                switch(v['Frame']), g.node('looks_setsizeto', {'SIZE': v['Size']}),
                *extras,
                g.node('looks_show')]
        g.procedure(DRAW, setup + [g.control('control_if_else', intersects, draw, [g.node('looks_hide')])])
        replacement = g.call(DRAW)
        original = g.blocks[goto]
        g.blocks[replacement].update(parent=original['parent'], next=None)
        g.blocks[goto] = g.blocks.pop(replacement)
        g.blocks[size].update(parent=None, next=None)
        g.blocks[costume].update(parent=None, next=None)
        g.blocks[show].update(parent=None, next=None)
        # Marker is the stable original call ID, with no extra runnable hat.
        g.blocks[MARKER] = {'opcode': 'procedures_call', 'next': None, 'parent': None,
                            'inputs': {}, 'fields': {}, 'shadow': False, 'topLevel': False,
                            'mutation': copy.deepcopy(g.blocks[goto]['mutation'])}
    press = next(t for t in project['targets'] if t['name'] == 'Title Intro PRESS START')
    press.update(x=0, y=0, size=100, direction=90, rotationStyle="don't rotate")
    press['costumes'][0].update(rotationCenterX=240, rotationCenterY=180, bitmapResolution=1)
    _install_star_raster(project, stage)
    result = {'version': 2, 'stars': 'native_128_world_quad_shared_depth_buffer', 'billboards': 'warp_transparent_anchor_then_visual_costume',
              'viewport': 'clip_visual_quad_without_clamping_world_track',
              'press_start': 'ROM HUD glyphs; native positions; 20 of 32 ticks'}
    project.setdefault('extensionData', {})['v13_title_effect_presentation'] = result
    return result


STAR_RASTER = 'TITLE | raster ROM star billboards into shared depth buffer'
STAR_RASTER_MARKER = 'v13_title_star_depth_raster'


def _install_star_raster(project, stage):
    """Render native camera-facing quads after the head, before presentation.

    draw_light/draw_shape_2d emit gd_vertex_star (-64,0)-(64,128), with
    G_RM_AA_ZB_TEX_EDGE. The engine already has reciprocal depth and RGB
    buffers; using them removes reference-view gates and sprite fencing,
    and allows the head to occlude a star per pixel during camera motion.
    Texture alpha is binary RGBA16; -1 is transparent, including black-safe
    alpha handling. Sampling uses the engine's existing nearest pixel grid.
    """
    render = intro_render_target(project)
    if STAR_RASTER_MARKER in render['blocks']:
        return
    g = Graph(render, stage)
    v = {n: g.var('TitleStarRaster.' + n, local=True) for n in (
        'Depth', 'InverseDepth', 'Width', 'Left', 'Bottom', 'X0', 'X1',
        'Y0', 'Y1', 'X', 'Y', 'Index', 'TX', 'TY', 'Pixel', 'FrameBase')}
    add = lambda a, b: g.op('add', a, b)
    sub = lambda a, b: g.op('subtract', a, b)
    mul = lambda a, b: g.op('multiply', a, b)
    div = lambda a, b: g.op('divide', a, b)
    res = g.var('res')
    width, height = g.var('screen width'), g.var('screen height')
    body = []
    for colour in ('Silver', 'Red'):
        prefix = 'TitleIntro.' + colour + 'Star'
        setup = [g.set(v['Depth'], g.var(prefix + 'CameraDepth')),
                 g.set(v['InverseDepth'], div(1, v['Depth'])),
                 g.set(v['Width'], div(div(49152, v['Depth']), res)),
                 g.set(v['Left'], div(sub(mul(g.var(prefix + 'ScreenX'), 1.5), div(mul(v['Width'], res), 2)), res)),
                 g.set(v['Bottom'], div(sub(360, mul(g.var(prefix + 'ScreenY'), 1.5)), res)),
                 g.set(v['X0'], g.math('ceiling', v['Left'])),
                 g.set(v['X1'], sub(g.math('ceiling', add(v['Left'], v['Width'])), 1)),
                 g.set(v['Y0'], g.math('ceiling', v['Bottom'])),
                 g.set(v['Y1'], sub(g.math('ceiling', add(v['Bottom'], v['Width'])), 1)),
                 g.set(v['FrameBase'], mul(g.op('mod', g.var('TitleIntro.Frame'), 8), 1024))]
        for n, limit, cmp in [('X0', 0, 'lt'), ('Y0', 0, 'lt'),
                              ('X1', sub(width, 1), 'gt'), ('Y1', sub(height, 1), 'gt')]:
            setup.append(g.control('control_if', g.op(cmp, v[n], limit), [g.set(v[n], limit)]))
        pixel = [g.set(v['TX'], g.math('floor', div(mul(sub(v['X'], v['Left']), 32), v['Width']))),
                 g.set(v['Index'], add(add(mul(v['Y'], width), v['X']), 1)),
                 g.set(v['Pixel'], g.item(prefix + 'Pixels', add(add(v['FrameBase'], mul(v['TY'], 32)), add(v['TX'], 1))))]
        write = [g.node('data_replaceitemoflist', {'INDEX': v['Index'], 'ITEM': v['InverseDepth']}, {'LIST': g.list('depth buffer')}),
                 g.node('data_replaceitemoflist', {'INDEX': v['Index'], 'ITEM': v['Pixel']}, {'LIST': g.list('tex buffer')})]
        pixel += [g.control('control_if', g.op('and', g.op('gt', v['Pixel'], -1),
                    g.op('gt', v['InverseDepth'], g.item('depth buffer', v['Index']))), write),
                  g.set(v['X'], 1, True)]
        rows = [g.set(v['TY'], sub(31, g.math('floor', div(mul(sub(v['Y'], v['Bottom']), 32), v['Width'])))),
                g.set(v['X'], v['X0']),
                g.control('control_repeat', add(sub(v['X1'], v['X0']), 1), pixel),
                g.set(v['Y'], 1, True)]
        setup += [g.set(v['Y'], v['Y0']),
                  g.control('control_if', g.op('and', g.op('gt', v['X1'], sub(v['X0'], 1)),
                                              g.op('gt', v['Y1'], sub(v['Y0'], 1))),
                            [g.control('control_repeat', add(sub(v['Y1'], v['Y0']), 1), rows)])]
        body.append(g.control('control_if', g.op('equals', g.var(prefix + 'ScreenVisible'), 1), setup))
    enabled = g.op('and', g.op('equals', g.var('TitleIntro.Active'), 1),
                          g.op('equals', g.var('TitleIntro.Phase'), 2))
    g.procedure(STAR_RASTER, [g.control('control_if', enabled, body)])
    # The Render actor invokes present only after drawing the render queue.
    g.insert_after('NA', [g.call(STAR_RASTER)])
    g.blocks[STAR_RASTER_MARKER] = {'opcode': 'looks_hide', 'next': None,
        'parent': None, 'inputs': {}, 'fields': {}, 'shadow': False, 'topLevel': False}
    for name in ('Title Intro Silver Star', 'Title Intro Red Star'):
        target = next(t for t in project['targets'] if t['name'] == name)
        target['visible'] = False
        # Keep original costume records available for private inspection, but
        # prevent double drawing above the shared software depth buffer.
        target['blocks']['hide0']['next'] = None
