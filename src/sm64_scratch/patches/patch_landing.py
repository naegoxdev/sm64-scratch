"""Honor ROM animation starts and the native cameraman object rotation.

Only Scratch logic and empty runtime tables are installed in the public BASE.
"""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _definition


def apply_landing_fixes(project):
    stage = next(t for t in project['targets'] if t['isStage'])
    targets = {t['name']: t for t in project['targets']}
    g = Graph(targets['Mario'], stage)
    starts = g.list('Rig.ActionInitialFrame')
    if 'landing_v10_request_start' not in g.blocks:
        initial = g.item(starts[0], g.var('Anim.ClipID'))
        node = g.set(g.var('Anim.Time'), initial)
        g.blocks['landing_v10_request_start'] = g.blocks.pop(node)
        g.blocks[initial]['parent'] = 'landing_v10_request_start'
        g.insert_after('VD', ['landing_v10_request_start'])
        # The pipe's airborne action must enter the actual landing controller.
        arg = next(iter(g.blocks['asp']['inputs']))
        g.blocks['asp']['inputs'][arg] = [1, [10, 'ACT_JUMP_LAND_STOP']]
    for name in ('Render', 'Title Intro Render'):
        rg = Graph(targets[name], stage)
        marker = 'landing_v10_lakitu_order'
        if marker not in rg.blocks:
            # C's Z-X-Y application order is Ry * Rx * Rz in column form.
            # Scratch quaternion multiplication names that product "yxz".
            model = rg.item('Loaded models', rg.op('add', rg.var('draw model id'), 1))
            node = rg.control('control_if', rg.op('equals', model, 'Intro Lakitu'),
                              [rg.set(rg.var('Render.RotationOrder'), 'yxz')])
            rg.blocks[marker] = rg.blocks.pop(node)
            for value in rg.blocks[marker]['inputs'].values():
                if isinstance(value[1], str) and value[1] in rg.blocks:
                    rg.blocks[value[1]]['parent'] = marker
            definition = _definition(rg, 'RENDER | select Chain Chomp rotation order')
            rg.insert_after(rg.blocks[definition]['next'], [marker])
    result = dict(version=1, animation_start='ROM start_frame on clip entry',
                  single_jump='Native clip 77; landing clip 78 starts at 22',
                  pipe_landing='ACT_JUMP_LAND_STOP instead of immediate idle',
                  lakitu_rotation='Native Ry * Rx * Rz, Scratch yxz')
    project.setdefault('extensionData', {}).setdefault('landing_animation_fix', {}).update(result)
    return result
