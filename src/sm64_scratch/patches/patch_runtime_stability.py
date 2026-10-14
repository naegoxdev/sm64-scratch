"""Cannon/session repairs (V23) with live transition rendering restored (V24)."""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _call, _definition, _replace_body
from sm64_scratch.patches.patch_bob_objectives import CREATE, EDIT, _tail, _put, _wrap


def _rename_cannon_centers(project):
    stage = next(t for t in project['targets'] if t['isStage'])
    mapping = {}
    for axis in 'XYZ':
        old_name, new_name = 'BOB.Cannon.' + axis, 'BOB.Cannon.Center' + axis
        rows = [(k, v) for k, v in stage['variables'].items() if v[0] == old_name]
        for key, row in rows:
            new_id = 'v23_scalar_' + new_name.replace('.', '_')
            stage['variables'][new_id] = [new_name, 0]
            del stage['variables'][key]
            mapping[key] = (new_name, new_id)
    def rewrite(value):
        if isinstance(value, list):
            if len(value) == 3 and value[0] == 12 and value[2] in mapping:
                name, key = mapping[value[2]];value[:] = [12, name, key]
            else:
                for child in value:rewrite(child)
        elif isinstance(value, dict):
            field = value.get('fields', {}).get('VARIABLE')
            if field and field[1] in mapping:field[:] = mapping[field[1]]
            for child in value.values():rewrite(child)
    for target in project['targets']:rewrite(target['blocks'])
    return len(mapping)


def apply_runtime_stability(project):
    repaired = _rename_cannon_centers(project)
    meta = project.setdefault('extensionData', {})
    _restore_live_wipes(project)
    if meta.get('runtime_stability_v23'):
        meta['runtime_stability_v23']['wipes'] = 'V24 restores V22 live drawing and presentation for every frame; wipe cache removed'
    targets = {t['name']: t for t in project['targets']}
    if meta.get('runtime_stability_v23') and any(
            b.get('mutation', {}).get('proccode') == 'WORLD | sync reusable BOB cannons'
            for b in targets['World + Assets']['blocks'].values()):
        return meta['runtime_stability_v23']
    stage = next(t for t in project['targets'] if t['isStage'])
    wg, mg = Graph(targets['World + Assets'], stage), Graph(targets['Mario'], stage)
    # Native bhvCannon lowers its home by 340 cm before the 68-tick rise.
    entry = _definition(mg, 'BOB CANNON | check entry')
    # Restrict edits to reachable nodes, keeping legacy detached graphs inert.
    def reachable(g, root):
        todo, seen = [root], set()
        while todo:
            key = todo.pop()
            if not key or key in seen:continue
            seen.add(key);b = g.blocks[key];todo.append(b.get('next'))
            for inp in b['inputs'].values():
                if isinstance(inp[1], str) and inp[1] in g.blocks:todo.append(inp[1])
        return seen
    for key in reachable(mg, mg.blocks[entry]['next']):
        b = mg.blocks[key]
        if b['opcode'] == 'data_setvariableto' and b.get('fields', {}).get('VARIABLE', [''])[0] == 'BOB.Cannon.CenterY':
            old = b['inputs']['VALUE'][1]
            expr = mg.op('subtract', old, 3.4)
            b['inputs']['VALUE'] = [3, expr, [10, '']];mg.blocks[expr]['parent'] = key
    # Five native recovery ticks replace an arbitrary three-second lockout.
    for b in mg.blocks.values():
        if b['opcode'] == 'data_setvariableto' and b.get('fields', {}).get('VARIABLE', [''])[0] == 'BOB.Cannon.Cooldown':
            if b['inputs'].get('VALUE') == [1, [10, '90']]:b['inputs']['VALUE'] = [1, [10, '5']]
    # The orbit remains above the opening while the mesh rises from underground.
    for key in reachable(mg, mg.blocks[_definition(mg, 'CAMERA | update isolated')]['next']):
        b = mg.blocks[key]
        name = b.get('fields', {}).get('VARIABLE', [''])[0]
        if b['opcode'] == 'data_setvariableto' and name in ('cam y', 'Camera64.FocusY'):
            value = b.get('inputs', {}).get('VALUE', [None, None])[1]
            if isinstance(value, str) and value in mg.blocks:
                text = str([mg.blocks[k] for k in reachable(mg, value)])
                if 'BOB.Cannon.Rise' in text and name == 'Camera64.FocusY':
                    expr = mg.op('add', mg.var('BOB.Cannon.CenterY'), 4.4)
                    b['inputs']['VALUE'] = [3, expr, [10, '']];mg.blocks[expr]['parent'] = key
                elif 'BOB.Cannon.CameraDistance' in text:
                    pass
                elif 'BOB.Cannon.Rise' in text and name == 'cam y':
                    # Distinguish the orbit shot from first-person aiming by its
                    # original +2 offset. Native first-person eye height is
                    # Mario's +350 cm placement plus the camera's +125 cm.
                    if "'2'" in text:
                        expr = mg.op('add', mg.var('BOB.Cannon.CenterY'), 5.4)
                        b['inputs']['VALUE'] = [3, expr, [10, '']];mg.blocks[expr]['parent'] = key
                    else:
                        expr = mg.op('add', mg.var('BOB.Cannon.CenterY'), 4.75)
                        b['inputs']['VALUE'] = [3, expr, [10, '']];mg.blocks[expr]['parent'] = key

    _cannon_world(wg)
    _reset_session(project, stage)
    _blink(project, stage)
    # Native act_5 finishes its sixteen 0x400 increments at sin(pi/2),
    # producing a 45-degree pitch. Preserve that initial aiming angle.
    targets['BOB Cannon Aim'].update(x=0, y=0, size=100, direction=90, rotationStyle='don\'t rotate')
    targets['BOB Cannon Aim']['costumes'][0].update(rotationCenterX=36, rotationCenterY=36)
    meta['runtime_stability_v23'] = dict(version=1, repaired_cannon_identifier_collisions=repaired,
        cannon='Native -340 cm home; full reset after firing; five-tick recovery; immutable position lists',
        reset='Flag press resets counts, collected lists, dropped coins, cannon state and number lifetime',
        blinking='Numeric frame toggle; explicit visible reset when invulnerability ends; retain shadow',
        wipes='V24 restores V22 live drawing and presentation for every frame; wipe cache removed',
        runtime_verified=False)
    return meta['runtime_stability_v23']


def _cannon_world(g):
    v = g.var
    i = v('BOB.Cannon.VisualIndex', local=True)
    idx = v('BOB.Cannon.VisualSceneIndex', local=True)
    type_ = v('BOB.Cannon.VisualType', local=True)
    y = v('BOB.Cannon.VisualY', local=True)
    pitch = v('BOB.Cannon.VisualPitch', local=True)
    yaw = v('BOB.Cannon.VisualYaw', local=True)
    write = lambda off, value: _put(g, g.op('add', idx, off), value)
    active = lambda: g.op('and', g.op('equals', v('BOB.Cannon.Active'), 1),
                                g.op('equals', i, v('BOB.Cannon.Index')))
    add=lambda value:g.node('data_addtolist',{'ITEM':value},{'LIST':g.list('BOB.Cannon.OpeningIndex')})
    join=lambda a,b:g.node('operator_join',{'STRING1':a,'STRING2':b})
    opening=[g.node('data_deletealloflist',fields={'LIST':g.list('BOB.Cannon.OpeningIndex')}),
        _call(g,'ASSET | load model %s',['BOB Cannon Opening']),g.set(i,1),
        g.control('control_repeat',g.length('BOB.Cannon.X'),[
            _call(g,CREATE,[join('BOB Cannon Opening ',i),'3d','BOB Cannon Opening',
                g.item('BOB.Cannon.X',i),g.op('add',g.item('BOB.Cannon.Y',i),.03),
                g.item('BOB.Cannon.Z',i),0,0,0,'']),
            add(g.op('subtract',g.length('Scene'),39)),g.set(i,1,True)]),
        g.call('SCENE | rebuild render queue')]
    g.procedure('WORLD | create BOB cannon openings',opening)
    # Far culling now uses camera distance, not Mario proximity, preventing
    # visible cannons from popping into existence as Mario approaches.
    near = g.op('and', g.op('lt', g.math('abs', g.op('subtract', v('cam x'), g.item('BOB.Cannon.X', i))), 90),
                      g.op('lt', g.math('abs', g.op('subtract', v('cam z'), g.item('BOB.Cannon.Z', i))), 90))
    setup = [g.set(type_, 'hidden'), g.control('control_if', near, [g.set(type_, '3d')]),
        g.set(y, g.op('subtract', g.item('BOB.Cannon.Y', i), 3.4)), g.set(pitch, 0),
        g.set(yaw, g.item('BOB.Cannon.Yaw', i)),
        g.control('control_if', active(), [g.set(y, g.op('add', v('BOB.Cannon.CenterY'), v('BOB.Cannon.Rise'))),
            g.set(pitch, v('BOB.Cannon.Pitch')), g.set(yaw, v('BOB.Cannon.AimYaw')),
            g.control('control_if_else', g.op('gt', v('BOB.Cannon.Timer'), 122),
                       [g.set(type_, 'hidden')], [g.set(type_, '3d')])])]
    # The small rim remains visible while the barrel retracts below terrain.
    setup.insert(2,g.set(idx,g.item('BOB.Cannon.OpeningIndex',i)))
    setup.insert(3,write(4,type_))
    for name, is_barrel in [('BOB.Cannon.BaseIndex', False), ('BOB.Cannon.BarrelIndex', True)]:
        setup.extend([g.set(idx, g.item(name, i)), write(4, type_), write(12, y),
                      write(16, pitch if is_barrel else 0), write(18, yaw)])
    setup.append(g.set(i, 1, True))
    edit = lambda name, type_: _call(g, EDIT, [name, 'type', type_, 'set'])
    g.procedure('WORLD | sync reusable BOB cannons', [g.set(i, 1),
        g.control('control_repeat', g.length('BOB.Cannon.BaseIndex'), setup),
        g.control('control_if_else', g.op('equals', v('BOB.Cannon.Active'), 1),
            [edit('Mario', 'hidden'), edit('Mario Shadow', 'hidden'), g.set(v('BOB.Cannon.WasHidden'), 1)],
            [g.control('control_if', g.op('equals', v('BOB.Cannon.WasHidden'), 1),
                [edit('Mario', '3d'), edit('Mario Shadow', '3d'), g.set(v('BOB.Cannon.WasHidden'), 0),
                 g.set(v('BOB.Cannon.Rise'), 0), g.set(v('BOB.Cannon.Timer'), 0)])])])
    # Keep the existing red-coin/star/number synchronization, replacing only
    # its two cannon branches. They are consecutive top-level statements.
    definition = _definition(g, 'WORLD | sync BOB objectives')
    key, nodes = g.blocks[definition]['next'], []
    while key:
        b = g.blocks[key];following = b.get('next')
        if b['opcode'].startswith('control_') and 'BOB.Cannon.Active' in str(
                [g.blocks[k] for k in _descendants(g, b['inputs'].get('CONDITION', [None, None])[1])]):
            if not any(g.blocks[n].get('mutation', {}).get('proccode') == 'WORLD | sync reusable BOB cannons' for n in nodes):
                nodes.append(g.call('WORLD | sync reusable BOB cannons'))
        else:nodes.append(key)
        key = following
    _replace_body(g, 'WORLD | sync BOB objectives', nodes)
    _tail(g, 'WORLD | create BOB objectives', [g.call('WORLD | create BOB cannon openings'),
                                             g.call('WORLD | sync reusable BOB cannons')])


def _descendants(g, key):
    if not isinstance(key, str) or key not in g.blocks:return []
    found, todo = [], [key]
    while todo:
        current = todo.pop()
        if current in found:continue
        found.append(current)
        for inp in g.blocks[current]['inputs'].values():
            if isinstance(inp[1], str) and inp[1] in g.blocks:todo.append(inp[1])
    return found


def _reset_session(project, stage):
    main = next(t for t in project['targets'] if t['name'] == 'Main Loop')
    g = Graph(main, stage)
    names = ('Coin.Count', 'BOB.RedCoin.Count', 'BOB.RedCoin.AudioSkip', 'BOB.Cannon.Active',
             'BOB.Cannon.Cooldown', 'BOB.Cannon.Timer', 'BOB.Cannon.Rise', 'BOB.Cannon.WasHidden',
             'BOB.RedStar.Visible', 'Render.DamageBlink', 'SM64.InvincTimer')
    body = [g.set(g.var(name), 0) for name in names]
    i = g.var('Session.ResetCoinIndex', local=True)
    for name, value in [('Coin.Collected', 0), ('BOB.RedCoin.Collected', 0),
                        ('BOB.Number.Age', -1), ('BOB.Goomba.CoinTangible', 0)]:
        body.extend([g.set(i, 1), g.control('control_repeat', g.length(name), [
            g.node('data_replaceitemoflist', {'INDEX': i, 'ITEM': value}, {'LIST': g.list(name)}),
            g.set(i, 1, True)])])
    # Run in the same flag initialization chain before the engine/title reset.
    g.insert_after('/U', body)
    for row in stage['variables'].values():
        if row[0] in names:row[1] = 0


def _blink(project, stage):
    g = Graph(next(t for t in project['targets'] if t['name'] == 'Render'), stage)
    blink = g.var('Render.DamageBlink')
    setter = next(k for k, b in g.blocks.items() if b['opcode'] == 'data_setvariableto'
                  and b.get('fields', {}).get('VARIABLE', [''])[0] == blink[1]
                  and b.get('parent') == 'PA')
    condition = g.op('and', g.op('gt', g.var('SM64.InvincTimer'), 2),
                            g.op('equals', g.var('TitleIntro.Active'), 0))
    toggle = g.control('control_if_else', g.op('equals', blink, 1), [g.set(blink, 0)], [g.set(blink, 1)])
    block = g.control('control_if_else', condition, [toggle], [g.set(blink, 0)])
    old = g.blocks[setter]
    g.blocks[block].update(parent=old['parent'], next=old['next'])
    g.blocks['PA']['next'] = block
    if old['next']:g.blocks[old['next']]['parent'] = block
    del g.blocks[setter]
    gate = g.blocks['gameplay_v14_damage_blink_gate']
    name = lambda: g.item('Scene', g.op('add', g.var('Object.Idx'), 3))
    damage = g.op('and', g.op('equals', name(), 'Mario'), g.op('equals', blink, 1))
    cannon = g.op('and', g.op('equals', g.var('BOB.Cannon.Active'), 1),
        g.op('or', g.op('equals', name(), 'Mario'), g.op('equals', name(), 'Mario Shadow')))
    predicate = g.op('not', g.op('or', damage, cannon))
    gate['inputs']['CONDITION'] = [2, predicate];g.blocks[predicate]['parent'] = 'gameplay_v14_damage_blink_gate'



def _restore_live_wipes(project):
    """Remove the V23 scene/present cache without changing wipe clocks.

    The cache's phase/level key cannot distinguish an incomplete loading image
    from a completed destination frame. Preserve V22's live render path and
    completion broadcast so masks follow the image that was actually drawn.
    This also migrates projects already patched with V23.
    """
    meta = project.setdefault('extensionData', {})
    stage = next(t for t in project['targets'] if t['isStage'])
    g = Graph(next(t for t in project['targets'] if t['name'] == 'Render'), stage)
    definition = _definition(g, 'RENDER | present')
    head = g.blocks[definition]['next']
    wrapper = g.blocks[head]
    if meta.get('live_transition_render_v24') and wrapper['opcode'] == 'pen_clear':
        return
    # V23 wrapped the original pen-clear/present chain in SUBSTACK2.
    if (wrapper['opcode'] == 'control_if_else'
            and 'Render.WipeFresh' in str(g.target.get('variables', {}))):
        condition = wrapper['inputs'].get('CONDITION', [None, None])[1]
        # Inspect only this guard, rather than unrelated render predicates.
        todo, seen = [condition], set()
        while todo:
            key = todo.pop()
            if not isinstance(key, str) or key not in g.blocks or key in seen:
                continue
            seen.add(key)
            for value in g.blocks[key].get('inputs', {}).values():
                todo.extend(value[1:])
        if any('Render.WipeFresh' in str(g.blocks[key]) for key in seen):
            original = wrapper['inputs']['SUBSTACK2'][1]
            if g.blocks[original]['opcode'] != 'pen_clear':
                raise ValueError('Unrecognized V23 present cache; original pen chain missing')
            g.blocks[definition]['next'] = original
            g.blocks[original]['parent'] = definition
    _replace_body(g, 'RENDER | draw scene or reuse wipe image',
                  [g.call('RENDER | draw scene')])
    meta['live_transition_render_v24'] = dict(
        version=1, baseline='V22 live scene and pen presentation on every completed frame',
        removed='V23 per-phase/per-level scene cache and present skip',
        clocks='Door, painting and death masks retain their previous clocks and completion acknowledgments',
        runtime_verified=False)
