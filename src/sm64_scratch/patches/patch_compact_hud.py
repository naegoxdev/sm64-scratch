"""Share ten ROM digit costumes across event-driven HUD number slots."""
from copy import deepcopy

from sm64_scratch.patches.patch_animation import Graph

COUNTERS = (
    ('Coins', 'Coin.Count', 57, 999),
    ('Stars', 'Star.Count', 168, 999),
    ('Lives', 'HUD.Lives', -159, 100),
)


def digit_target_name(counter, slot):
    root = 'SM64 HUD ' + counter
    return root if slot == 1 else root + ' Digit ' + str(slot)


def apply_compact_hud(project):
    meta = project.setdefault('extensionData', {})
    marker = 'compact_hud_v20'
    targets = {t['name']: t for t in project['targets']}
    if meta.get(marker, {}).get('version') == 2 and all(
            digit_target_name(counter, slot) in targets
            for counter, *_ in COUNTERS for slot in range(1, 4)) and all(
            any(b.get('mutation', {}).get('proccode') == 'HUD | refresh number when changed'
                for b in targets[digit_target_name(counter, 1)]['blocks'].values())
            for counter, *_ in COUNTERS):
        return meta[marker]
    stage = next(t for t in project['targets'] if t.get('isStage'))
    targets = {t['name']: t for t in project['targets']}
    for counter, source, base_x, maximum in COUNTERS:
        original = targets[digit_target_name(counter, 1)]
        project['targets'] = [t for t in project['targets'] if t['name'] not in
                             [digit_target_name(counter, 2), digit_target_name(counter, 3)]]
        by_name = {c['name']: c for c in original['costumes']}
        digits = [dict(deepcopy(by_name[str(n)]), bitmapResolution=1,
                       rotationCenterX=0, rotationCenterY=12) for n in range(10)]
        active_name = 'HUD.' + counter + '.Active'
        text_name = 'HUD.' + counter + '.Text'
        x_name = 'HUD.' + counter + '.X'
        event_id = 'compact_hud_' + counter.lower()
        event = 'HUD DIGITS ' + counter.upper()
        stage['broadcasts'][event_id] = event
        slots = []
        for slot in range(1, 4):
            target = deepcopy(original)
            target.update(name=digit_target_name(counter, slot), blocks={},
                variables={}, lists={}, comments={}, costumes=deepcopy(digits),
                sounds=[], currentCostume=0, x=base_x + (slot-1)*18, y=145.5,
                size=100, visible=False, direction=90, rotationStyle="don't rotate")
            g = Graph(target, stage)
            active = g.var(active_name, 0)
            text = g.var(text_name, '')
            x = g.var(x_name, base_x)
            valid = g.op('and', g.op('equals', active, 1),
                         g.op('not', g.op('lt', g.node('operator_length', {'STRING':text}), slot)))
            draw = [g.node('looks_cleargraphiceffects'),
                g.node('looks_switchcostumeto', {'COSTUME':g.node('operator_letter_of',
                    {'LETTER':slot, 'STRING':text})}),
                g.node('motion_gotoxy', {'X':g.op('add', x, (slot-1)*18), 'Y':145.5}),
                g.node('looks_show')]
            g.procedure('HUD | present digit', [g.control('control_if_else', valid,
                                                        draw, [g.node('looks_hide')])])
            g.hat('event_whenbroadcastreceived', [g.call('HUD | present digit')],
                  [event, event_id])
            if slot == 1:
                last = g.var('HUD.LastValue', -1, local=True)
                raw = g.var('HUD.LastSource', -1, local=True)
                value = g.var('HUD.Value', 0, local=True)
                def notify():
                    key = g.node('event_broadcast')
                    g.blocks[key]['inputs'] = {'BROADCAST_INPUT':[1,[11,event,event_id]]}
                    return key
                visible = g.op('and', g.op('equals', g.var('HUD.NativeReady'), 1),
                               g.op('equals', g.var('TitleIntro.Active'), 0))
                changed = g.op('or', g.op('not', g.op('equals', value, last)),
                               g.op('equals', active, 0))
                position = (g.op('subtract',base_x,g.op('multiply',21,
                    g.op('not',g.op('lt',value,100)))) if counter == 'Stars' else base_x)
                update = [g.set(last,value),g.set(active,1),
                    g.set(text,g.node('operator_join',{'STRING1':'','STRING2':value})),
                    g.set(x,position),notify()]
                source_changed = g.op('or',
                    g.op('not', g.op('equals', g.var(source), raw)),
                    g.op('equals', active, 0))
                refresh = [g.set(raw, g.var(source)),
                        g.set(value,g.math('floor',g.var(source))),
                        g.control('control_if',g.op('lt',value,0),[g.set(value,0)]),
                        g.control('control_if',g.op('gt',value,maximum),[g.set(value,maximum)]),
                        g.control('control_if',changed,update)]
                g.procedure('HUD | refresh number when changed', [
                    g.control('control_if_else', visible, [
                        g.control('control_if', source_changed, refresh)], [
                        g.control('control_if',g.op('equals',active,1),[
                            g.set(active,0),notify()])])])
                g.hat('event_whenbroadcastreceived', [g.call('HUD | refresh number when changed')],
                      ['ENGINE FRAME RENDERED','porter_frame_rendered'])
                def reset():
                    return [g.set(active,0),g.set(last,-1),g.set(raw,-1),g.set(text,''),g.node('looks_hide')]
            else:
                def reset():
                    return [g.node('looks_hide')]
            g.hat('event_whenflagclicked',reset())
            g.hat('event_whenbroadcastreceived',reset(),['TITLE INTRO RUN','titleintro_bc_run'])
            slots.append(target)
        index = project['targets'].index(original)
        project['targets'][index:index+1] = slots
    # Adjacent slots retain the same layer as their old counter: later digits
    # composite above earlier ones, matching the former PNG alpha composition.
    report = dict(version=2, unique_digit_images=10, number_sprites=9,
        number_costume_slots=90, previous_number_costume_slots=2101,
        update_policy='Three value/visibility checks per completed render; digit events only on changes; no polling loops or clones',
        digit_advance=18, y=145.5, coins_limit=999, stars_limit=999, lives_limit=100,
        verification='Targeted media and VM checks are documented in STATUS.md; full-game FPS is not measured')
    meta[marker] = report
    return report
