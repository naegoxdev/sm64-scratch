"""Final native HUD visibility and Castle Grounds moving-texture fixes.

This patch is intentionally narrow:
- the coin icon/count is shown only in implemented courses (currently BOB),
  matching SM64's HUD_DISPLAY_FLAG_COIN_COUNT course gate;
- Castle Grounds movtex face ranges are evaluated relative to Arena's first
  face instead of as absolute global face indices, so the waterfall/water
  animation survives the persistent model cache and arbitrary load order.
"""
from copy import deepcopy

from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition, _replace_body

MARKER = 'course_hud_waterfall_v29'


def _ensure_hud_strip_costumes(target):
    names = [c['name'] for c in target.get('costumes', [])]
    required = ('HUD icons', 'HUD icons no star multiplier',
                'HUD icons no coin', 'HUD icons no coin no star multiplier')
    if not target.get('costumes'):
        raise RuntimeError('SM64 HUD has no costume placeholders')
    template = deepcopy(target['costumes'][0])
    by_name = {c['name']: c for c in target['costumes']}
    target['costumes'] = []
    for name in required:
        costume = deepcopy(by_name.get(name, template))
        costume.update(name=name, bitmapResolution=1, rotationCenterX=240, rotationCenterY=180)
        target['costumes'].append(costume)
    return required


def _patch_hud(project, stage):
    targets = {t['name']: t for t in project['targets']}
    hud = targets['SM64 HUD']
    _ensure_hud_strip_costumes(hud)
    g = Graph(hud, stage)
    strip = g.var('HUD.StripCostume', local=True)

    def star_under_100():
        return g.op('lt', g.var('Star.Count'), 100)

    with_coin = g.control('control_if_else', star_under_100(),
        [g.set(strip, 'HUD icons')],
        [g.set(strip, 'HUD icons no star multiplier')])
    without_coin = g.control('control_if_else', star_under_100(),
        [g.set(strip, 'HUD icons no coin')],
        [g.set(strip, 'HUD icons no coin no star multiplier')])
    course_icons = g.control('control_if_else',
        g.op('equals', g.var('Level.ID'), 'BOB'), [with_coin], [without_coin])
    valid = g.op('and', g.op('equals', g.var('HUD.NativeReady'), 1),
                 g.op('equals', g.var('TitleIntro.Active'), 0))
    body = [g.control('control_if_else', valid, [
                g.node('looks_cleargraphiceffects'), course_icons,
                g.node('looks_switchcostumeto', {'COSTUME': strip}),
                g.node('motion_gotoxy', {'X': 0, 'Y': 0}),
                g.node('looks_show')], [g.node('looks_hide')])]
    _replace_body(g, 'HUD | present native display', body)

    # The first coin digit owns the shared Active/Text state. Extending its
    # existing visibility predicate hides all three digit slots via its normal
    # broadcast path without touching stars or lives.
    coins = targets['SM64 HUD Coins']
    cg = Graph(coins, stage)
    definition = _definition(cg, 'HUD | refresh number when changed')
    outer = cg.blocks[cg.blocks[definition]['next']]
    if outer.get('opcode') != 'control_if_else':
        raise RuntimeError('Unexpected compact coin HUD procedure')
    old = outer['inputs']['CONDITION'][1]
    if not isinstance(old, str) or old not in cg.blocks:
        raise RuntimeError('Missing compact coin HUD visibility predicate')
    course = cg.op('equals', cg.var('Level.ID'), 'BOB')
    combined = cg.op('and', old, course)
    outer['inputs']['CONDITION'] = [2, combined]
    cg.blocks[combined]['parent'] = cg.blocks[definition]['next']


def _patch_movtex(project, stage):
    render = next(t for t in project['targets'] if t['name'] == 'Render')
    g = Graph(render, stage)
    b = g.blocks
    expected = {
        'p12r_000917': (781, 799),   # waterfall: local faces 782..798
        'p12r_001005': (798, 801),   # moat quad: local faces 799..800
        'p12r_001093': (800, 803),   # lake quad: local faces 801..802
    }
    for control_id, (lo, hi) in expected.items():
        if control_id not in b or b[control_id].get('opcode') != 'control_if':
            raise RuntimeError('Castle Grounds movtex block not found: ' + control_id)

        def local_face():
            # idx1 is a global face-list index. The Arena model can be appended
            # after cached BOB/other models, so compare its model-local index.
            return g.op('add', g.op('subtract', g.var('idx1'), g.var('draw first face')), 1)

        cond = g.op('and', g.op('gt', local_face(), lo), g.op('lt', local_face(), hi))
        b[control_id]['inputs']['CONDITION'] = [2, cond]
        b[cond]['parent'] = control_id

    # Restrict all three moving-surface branches to the Arena model as well as
    # the existing GROUNDS level test. This prevents another cached model with
    # a large local face count from ever receiving the UV transform.
    outer_id = 'p12r_001096'
    if outer_id not in b or b[outer_id].get('opcode') != 'control_if':
        raise RuntimeError('Castle Grounds movtex outer guard not found')
    old = b[outer_id]['inputs']['CONDITION'][1]
    model_name = g.item('Loaded models', g.op('add', g.var('engine.modelindex'), 1))
    arena = g.op('equals', model_name, 'Arena')
    combined = g.op('and', old, arena)
    b[outer_id]['inputs']['CONDITION'] = [2, combined]
    b[combined]['parent'] = outer_id


def apply_course_hud_waterfall(project):
    meta = project.setdefault('extensionData', {})
    current = meta.get(MARKER, {})
    if current.get('version') == 1:
        return current
    stage = next(t for t in project['targets'] if t.get('isStage'))
    _patch_hud(project, stage)
    _patch_movtex(project, stage)
    report = {
        'version': 1,
        'coin_hud': 'coin icon and number only when Level.ID == BOB (implemented course)',
        'waterfall': 'native speed-70 U scroll retained; movtex face ranges made Arena-local',
        'water_surfaces': 'moat/lake animated face ranges also made Arena-local',
        'runtime_verified': False,
    }
    meta[MARKER] = report
    return report
