"""Merge private gameplay logic with an existing assetless title base.

No private model, texture, animation or sound bytes are exported. The
synchronizer strips all list data and replaces media after this merge.
"""
from copy import deepcopy
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.render_targets import INTRO_RENDER_NAME, intro_render_target


def _gate_renderer(target, stage, active):
    """Dispatch once per broadcast, not once per face or rasterized pixel."""
    g = Graph(target, stage)
    count = 0
    for key, hat in list(g.blocks.items()):
        if hat['opcode'] != 'event_whenbroadcastreceived':
            continue
        label = hat.get('fields', {}).get('BROADCAST_OPTION', [''])[0]
        if label not in ('MODULE RENDER INIT', 'MODULE RENDER FRAME'):
            continue
        prefix = 'gameplay_merge_render_' + label.replace(' ', '_')
        if hat['next'] == prefix:
            count += 1
            continue
        body = hat['next']
        if not body:
            raise ValueError('Empty render broadcast: ' + label)
        guard = g.node('control_if', {'CONDITION': g.op('equals', g.var('TitleIntro.Active'), active), 'SUBSTACK': body})
        g.blocks[prefix] = g.blocks.pop(guard)
        for value in g.blocks[prefix]['inputs'].values():
            if isinstance(value[1], str) and value[1] in g.blocks:
                g.blocks[value[1]]['parent'] = prefix
        hat['next'] = prefix
        g.blocks[prefix]['parent'] = key
        count += 1
    if count != 2:
        raise ValueError('Expected exactly two engine render broadcasts')


def merge_gameplay_with_intro(base, private):
    """Retain current title implementation and accept the supplied gameplay."""
    old = {t['name']: t for t in base['targets']}
    new = {t['name']: t for t in private['targets']}
    if 'Title Intro Controller' not in old:
        raise ValueError('Gameplay-only migration needs a base containing the title')
    if 'Title Intro Controller' in new:
        raise ValueError('Source already has a title; use normal synchronization')
    missing = {'Stage', 'Main Loop', 'Render', 'Mario', 'World + Assets'} - new.keys()
    if missing:
        raise ValueError('Missing gameplay targets: ' + ', '.join(sorted(missing)))
    merged = deepcopy(private)
    targets = {t['name']: t for t in merged['targets']}
    stage = next(t for t in merged['targets'] if t.get('isStage'))
    # The gameplay source no longer defines the title's state/buffers.
    for field in ('variables', 'lists', 'broadcasts'):
        for identity, definition in old['Stage'].get(field, {}).items():
            stage.setdefault(field, {}).setdefault(identity, deepcopy(definition))
    for identity, block in old['Stage']['blocks'].items():
        if identity not in ('notice_greenflag', 'notice_switchbackdrop'):
            stage['blocks'].setdefault(identity, deepcopy(block))
    # Title scripts select these backdrops by name. Their blank media is
    # retained from the assetless base; the builder reconstructs it locally.
    costume_names = {costume['name'] for costume in stage['costumes']}
    for costume in old['Stage']['costumes']:
        if costume['name'].startswith('Title Intro ') and costume['name'] not in costume_names:
            stage['costumes'].append(deepcopy(costume))
            costume_names.add(costume['name'])
    # Keep authored gameplay startup, with the existing title gate around it.
    loop = targets['Main Loop']['blocks']
    for identity in ('/U', 'CG', 'CH'):
        if identity not in loop:
            raise ValueError('Gameplay startup bindings changed: ' + identity)
    if loop['/U']['next'] != 'CG' or loop['CG']['next'] != 'CH':
        raise ValueError('Unrecognized gameplay startup; inspect before migrating')
    for identity in ('titleintro_main_gate', 'v23_gameplay_fade_in_broadcast'):
        loop[identity] = deepcopy(old['Main Loop']['blocks'][identity])
    loop['/U']['next'] = 'titleintro_main_gate'
    loop['CG']['parent'] = 'titleintro_main_gate'
    loop['CG']['next'] = 'v23_gameplay_fade_in_broadcast'
    loop['CH']['parent'] = 'v23_gameplay_fade_in_broadcast'
    # Reuse only the presentation guards; gameplay HUD logic remains native.
    for name in ('SM64 HUD', 'SM64 HUD Coins', 'SM64 HUD Stars'):
        if name in targets:
            for key, block in old[name]['blocks'].items():
                if key.startswith('titleintro_hud_'):
                    targets[name]['blocks'][key] = deepcopy(block)
    if 'SM64 Power Meter' in targets:
        meter = targets['SM64 Power Meter']['blocks']
        if meter.get('h12hud_0004', {}).get('fields') != old['SM64 Power Meter']['blocks']['h12hud_0004']['fields']:
            raise ValueError('Power-meter presentation bindings changed')
        for key in ('h12hud_0003', 'h12hud_0004', 'titleintro_power_hide_or', 'titleintro_power_active_eq', 'titleintro_power_active_var'):
            meter[key] = deepcopy(old['SM64 Power Meter']['blocks'][key])
    retained = []
    for source in base['targets']:
        if source['name'].startswith('Title Intro ') and source['name'] != INTRO_RENDER_NAME and source['name'] not in targets:
            merged['targets'].append(deepcopy(source))
            retained.append(source['name'])
    # Preserve the existing title pipeline, but never run it during gameplay.
    renderer = deepcopy(intro_render_target(base))
    renderer['name'] = INTRO_RENDER_NAME
    _gate_renderer(renderer, stage, 1)
    _gate_renderer(targets['Render'], stage, 0)
    merged['targets'].append(renderer)
    report = {
        'mode': 'gameplay_only_with_preserved_intro',
        'retained_intro_targets': retained,
        'gameplay_renderer': 'Render',
        'intro_renderer': INTRO_RENDER_NAME,
        'renderer_dispatch': 'Two exclusive broadcast gates; no title face/pixel work during gameplay',
        'source_gameplay_targets': len(private['targets']),
        'merged_targets_before_audio': len(merged['targets']),
    }
    base.setdefault('extensionData', {})['gameplay_source_merge'] = report
    return merged, report
