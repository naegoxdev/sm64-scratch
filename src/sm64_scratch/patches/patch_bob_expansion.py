"""ROM-driven BOB coins, loot, posts, local warps and reward cameras (V23).

All gameplay additions use the existing simulation and scene phases. No
clones, independent polling loops or embedded ROM assets are added to BASE.
"""
from sm64_scratch.patches.patch_animation import Graph
from sm64_scratch.patches.patch_arrival_flight import _call, _definition, _replace_body
from sm64_scratch.patches.patch_bob_objectives import CREATE, EDIT, ACTION, ANIM, STAR, RED_STAR, _tail, _put, _wrap, _broadcast

HUNDRED_STAR = 'BOB Hundred Coins Star'


def apply_bob_expansion(project):
    meta=project.setdefault('extensionData',{})
    targets={t['name']:t for t in project['targets']};stage=next(t for t in project['targets'] if t['isStage'])
    wg,mg=Graph(targets['World + Assets'],stage),Graph(targets['Mario'],stage)
    if meta.get('bob_expansion_v23') and any(b.get('mutation',{}).get('proccode')=='BOB | tick expanded objectives' for b in mg.blocks.values()):return meta['bob_expansion_v23']
    _world(wg)
    _collect(mg)
    _loot(mg)
    _teleports(mg)
    _reward_camera(mg,wg)
    _camera(mg)
    mg.procedure('BOB | tick expanded objectives', [mg.control('control_if', mg.op('equals',mg.var('Level.ID'),'BOB'),[
        mg.call('BOB | collect yellow coins'), mg.call('BOB | update loot sources'),
        mg.call('BOB | check local warps'), mg.call('BOB | queue reward cameras'),mg.call('BOB | collect hundred-coin star')])])
    mg.insert_after('+=',[mg.call('BOB | tick expanded objectives')])
    # Time-stop cutscenes replace only the action body; the engine's clock,
    # health/audio observer and frame pipeline continue to advance normally.
    _wrap(mg,'SM64 | execute mario action',mg.op('or',mg.op('gt',mg.var('BOB.Warp.Phase'),0),
         mg.op('equals',mg.var('BOB.StarSpawn.Active'),1)),[
             mg.control('control_if_else',mg.op('gt',mg.var('BOB.Warp.Phase'),0),
                [mg.call('BOB | advance local warp')],[mg.call('BOB | advance reward camera')])])
    _tail(wg,'WORLD | create BOB objectives',[wg.call('WORLD | create expanded BOB objects')])
    _tail(wg,'WORLD | sync BOB objectives',[wg.call('WORLD | sync expanded BOB objects')])
    _teleport_overlay(project)
    # New session progress resets; ordinary scene reloads keep star ownership.
    reset=('Star.BOB7Collected','BOB.HundredStar.Visible','BOB.HundredStar.Spawned',
           'BOB.StarSpawn.Active','BOB.StarSpawn.PendingRed','BOB.StarSpawn.Pending100',
           'BOB.Warp.Phase','BOB.Warp.Lock','BOB.Warp.Timer')
    g=Graph(targets['Main Loop'],stage)
    g.insert_after('/U',[g.set(g.var(name),0) for name in reset])
    for row in stage['variables'].values():
        if row[0] in reset:row[1]=0
    meta['bob_expansion_v23']=dict(version=1,placed_yellow_coins=68,post_rewards=25,box_rewards=9,
        bobomb_rewards=12,existing_goomba_rewards=11,total_yellow_coins=125,
        teleport_pairs=2,red_star_camera=True,hundred_coin_star=True,hundred_coin_star_no_level_exit=True,
        runtime_verified=False,limitations=['Bob-omb movement/explosion is a simplified course interaction.',
        'Koopa and its blue coin are not restored; all native yellow-coin sources are represented.',
        'Local teleports use a brief white overlay because the pen renderer has no general Mario opacity.'])
    return meta['bob_expansion_v23']


def _world(g):
    # Retain decoded source identifiers even when runtime lookup uses indices.
    g.list('BOB.Loot.Group')
    v=g.var;i=v('BOB.Expanded.Index',local=True);idx=v('BOB.Expanded.SceneIndex',local=True)
    join=lambda a,b:g.node('operator_join',{'STRING1':a,'STRING2':b})
    add=lambda name,value:g.node('data_addtolist',{'ITEM':value},{'LIST':g.list(name)})
    clear=lambda name:g.node('data_deletealloflist',fields={'LIST':g.list(name)})
    create=lambda name,model,x,y,z,type_='3d':_call(g,CREATE,[name,type_,model,x,y,z,0,0,0,''])
    edit=lambda name,field,val:_call(g,EDIT,[name,field,val,'set'])
    setup=[g.set(v(name),0) for name in ('Coin.Count','BOB.HundredStar.Visible','BOB.HundredStar.Spawned',
        'BOB.StarSpawn.Active','BOB.StarSpawn.PendingRed','BOB.StarSpawn.Pending100','BOB.Warp.Phase','BOB.Warp.Lock')]
    work={'BOB.Yellow.':['SceneIndex','Collected'],
          'BOB.Loot.':['SceneIndex','State','Age','X','Y','Z','VelY'],
          'BOB.LootGroup.':['Triggered'],
          'BOB.Post.':['SceneIndex','Angle','TotalAngle','Timer'],
          'BOB.Box.':['SceneIndex'],
          'BOB.Bomb.':['BodyIndex','FootLIndex','FootRIndex','EyeIndex','KeyIndex','Fuse']}
    for prefix,fields in work.items():
        for name in fields:setup.append(clear(prefix+name))
    for n in range(1,5):setup.append(_call(g,'ASSET | load model %s',[f'BOB Yellow Coin Frame {n}']))
    setup.append(_call(g,'ASSET | load model %s',['BOB Black Bob-omb Body']))
    for prefix,model,type_ in [('BOB.Yellow.','BOB Yellow Coin Frame 1','3d'),('BOB.Loot.','BOB Yellow Coin Frame 1','hidden')]:
        home='Home' if prefix=='BOB.Loot.' else ''
        body=[create(join(prefix,i),model,*[g.item(prefix+home+a,i) for a in 'XYZ'],type_),
              add(prefix+'SceneIndex',g.op('subtract',g.length('Scene'),39))]
        if home:
            for name,val in [('State',0),('Age',0),('VelY',0)]+[(a,g.item(prefix+home+a,i)) for a in 'XYZ']:body.append(add(prefix+name,val))
        else:body.append(add(prefix+'Collected',0))
        setup.extend([g.set(i,1),g.control('control_repeat',g.length(prefix+home+'X'),[*body,g.set(i,1,True)])])
    setup.extend([g.set(i,1),g.control('control_repeat',g.length('BOB.LootGroup.Start'),[add('BOB.LootGroup.Triggered',0),g.set(i,1,True)])])
    # Four native wooden posts, scaled to the behavior's 50 percent size.
    post=[g.control('control_if_else',g.op('lt',i,5),[
        create(join('BOB Wooden Post ',i),'Chain Chomp Post',*[g.item('BOB.Post.'+a,i) for a in 'XYZ']),
        add('BOB.Post.SceneIndex',g.op('subtract',g.length('Scene'),39)),
        *[edit(join('BOB Wooden Post ',i),a,.5) for a in ('sx','sy','sz')]],
        [add('BOB.Post.SceneIndex',0)])]
    for name in ('Angle','TotalAngle','Timer'):post.append(add('BOB.Post.'+name,0))
    setup.extend([g.set(i,1),g.control('control_repeat',g.length('BOB.Post.X'),[*post,g.set(i,1,True)])])
    # Replace the two existing start boxes to prevent duplicate overlapping meshes.
    setup.extend([edit('BOB Start Box Three Coins','type','hidden'),edit('BOB Start Small Box','type','hidden')])
    setup.extend([g.set(i,1),g.control('control_repeat',g.length('BOB.Box.X'),[
        create(join('BOB Reward Box ',i),g.item('BOB.Box.Model',i),*[g.item('BOB.Box.'+a,i) for a in 'XYZ']),
        add('BOB.Box.SceneIndex',g.op('subtract',g.length('Scene'),39)),g.set(i,1,True)])])
    bombs=[]
    for name,model in [('Body','BOB Black Bob-omb Body'),('FootL','BOB Buddy Foot L'),('FootR','BOB Buddy Foot R'),
                       ('Eye','BOB Buddy Eyes Open'),('Key','BOB Buddy Key')]:
        bombs.extend([create(join('BOB Bomb '+name+' ',i),model,*[g.item('BOB.Bomb.'+a,i) for a in 'XYZ']),
                      add('BOB.Bomb.'+name+'Index',g.op('subtract',g.length('Scene'),39))])
    setup.extend([g.set(i,1),g.control('control_repeat',g.length('BOB.Bomb.X'),[*bombs,add('BOB.Bomb.Fuse',0),g.set(i,1,True)]),
        create(HUNDRED_STAR,'Power Star',0,0,0,'hidden'),g.set(v('BOB.HundredStar.SceneIndex'),g.op('subtract',g.length('Scene'),39)),
        g.control('control_if',g.op('equals',v('Star.BOB7Collected'),1),[edit(HUNDRED_STAR,'model','Power Star Collected')]),
        g.call('SCENE | rebuild render queue')])
    g.procedure('WORLD | create expanded BOB objects',setup)
    write=lambda off,value:_put(g,g.op('add',idx,off),value)
    model=g.node('operator_join',{'STRING1':'BOB Yellow Coin Frame ','STRING2':g.op('add',g.op('mod',g.math('floor',g.op('divide',v('SM64.SimulationFrame'),2)),4),1)})
    modelvar=v('BOB.Expanded.CoinModel',local=True)
    near=lambda prefix:g.op('and',g.op('lt',g.math('abs',g.op('subtract',v('cam x'),g.item(prefix+'X',i))),50),
                                     g.op('lt',g.math('abs',g.op('subtract',v('cam z'),g.item(prefix+'Z',i))),50))
    sync=[g.set(modelvar,model),g.set(i,1),g.control('control_repeat',g.length('BOB.Yellow.SceneIndex'),[
        g.set(idx,g.item('BOB.Yellow.SceneIndex',i)),g.control('control_if_else',g.op('and',
            g.op('equals',g.item('BOB.Yellow.Collected',i),0),near('BOB.Yellow.')),
            [write(4,'3d'),write(6,modelvar),write(18,v('cam ry'))],[write(4,'hidden')]),g.set(i,1,True)]),
        g.set(i,1),g.control('control_repeat',g.length('BOB.Loot.SceneIndex'),[
        g.set(idx,g.item('BOB.Loot.SceneIndex',i)),g.control('control_if_else',g.op('and',
            g.op('equals',g.item('BOB.Loot.State',i),1),near('BOB.Loot.')),
            [write(4,'3d'),write(6,modelvar),write(18,v('cam ry')),
             *[write(off,g.item('BOB.Loot.'+a,i)) for off,a in [(10,'X'),(12,'Y'),(14,'Z')]]],[write(4,'hidden')]),g.set(i,1,True)])]
    sync.extend([g.set(i,1),g.control('control_repeat',g.length('BOB.Box.SceneIndex'),[
        g.set(idx,g.item('BOB.Box.SceneIndex',i)),g.control('control_if',g.op('equals',g.item('BOB.LootGroup.Triggered',g.op('add',i,5)),1),[write(4,'hidden')]),g.set(i,1,True)])])
    bombsync=[]
    for name in ('Body','FootL','FootR','Eye','Key'):
        bombsync.extend([g.set(idx,g.item('BOB.Bomb.'+name+'Index',i)),g.control('control_if_else',
            g.op('and',g.op('equals',g.item('BOB.LootGroup.Triggered',g.op('add',i,8)),0),near('BOB.Bomb.')),
            [write(4,'3d'),write(18,v('cam ry') if name=='Body' else 0)],[write(4,'hidden')])])
    sync.extend([g.set(i,1),g.control('control_repeat',g.length('BOB.Bomb.X'),[*bombsync,g.set(i,1,True)]),
        g.control('control_if',g.op('equals',v('BOB.HundredStar.Visible'),1),[
            g.set(idx,v('BOB.HundredStar.SceneIndex')),
            g.control('control_if',g.op('equals',v('Star.State'),0),[
                write(4,'3d'),*[write(off,v('BOB.HundredStar.'+a)) for off,a in [(10,'X'),(12,'Y'),(14,'Z')]],
                write(18,g.op('multiply',v('SM64.SimulationFrame'),11.25))])]),
        g.call('WORLD | sync reward star flight')])
    g.procedure('WORLD | sync expanded BOB objects',sync)


def _collect(g):
    v=g.var;i=v('BOB.Pickup.Index',local=True)
    close=lambda prefix:g.op('and',g.op('lt',g.op('add',
        g.op('multiply',g.op('subtract',v('SM64.PosX'),g.item(prefix+'X',i)),g.op('subtract',v('SM64.PosX'),g.item(prefix+'X',i))),
        g.op('multiply',g.op('subtract',v('SM64.PosZ'),g.item(prefix+'Z',i)),g.op('subtract',v('SM64.PosZ'),g.item(prefix+'Z',i)))),1.8769),
        g.op('lt',g.math('abs',g.op('subtract',g.op('subtract',v('SM64.PosY'),g.item(prefix+'Y',i)),-.65)),1.25))
    replace=lambda name,value:g.node('data_replaceitemoflist',{'INDEX':i,'ITEM':value},{'LIST':g.list(name)})
    pickup=lambda:[g.set(v('Coin.Count'),1,True),g.set(v('SM64.HealCounter'),4,True)]
    valid=g.op('and',g.op('equals',v('BOB.StarSpawn.Active'),0),g.op('and',g.op('equals',v('BOB.Warp.Phase'),0),
            g.op('not',g.op('equals',v('SM64.ActionGroup'),'CUTSCENE'))))
    g.procedure('BOB | collect yellow coins',[g.control('control_if',valid,[
        g.set(i,1),g.control('control_repeat',g.length('BOB.Yellow.X'),[
            g.control('control_if',g.op('and',g.op('equals',g.item('BOB.Yellow.Collected',i),0),close('BOB.Yellow.')),
                [replace('BOB.Yellow.Collected',1),*pickup()]),g.set(i,1,True)]),
        g.set(i,1),g.control('control_repeat',g.length('BOB.Loot.State'),[
            g.control('control_if',g.op('equals',g.item('BOB.Loot.State',i),1),[
                g.control('control_if',g.op('and',g.op('gt',g.item('BOB.Loot.Age',i),8),close('BOB.Loot.')),
                    [replace('BOB.Loot.State',2),*pickup()]),
                g.control('control_if',g.op('equals',g.item('BOB.Loot.State',i),1),[
                    replace('BOB.Loot.Age',g.op('add',g.item('BOB.Loot.Age',i),1)),
                    replace('BOB.Loot.Y',g.op('add',g.item('BOB.Loot.Y',i),g.item('BOB.Loot.VelY',i))),
                    replace('BOB.Loot.VelY',g.op('subtract',g.item('BOB.Loot.VelY',i),.04)),
                    g.control('control_if',g.op('lt',g.item('BOB.Loot.Y',i),g.item('BOB.Loot.HomeY',i)),[
                        replace('BOB.Loot.Y',g.item('BOB.Loot.HomeY',i)),replace('BOB.Loot.VelY',0)])])]),g.set(i,1,True)])])])


def _loot(g):
    v=g.var;i=v('BOB.Source.Index',local=True);group=v('BOB.Loot.TriggerGroup');j=v('BOB.Loot.SpawnIndex',local=True)
    put=lambda name,index,value:g.node('data_replaceitemoflist',{'INDEX':index,'ITEM':value},{'LIST':g.list(name)})
    spawn=[g.set(j,g.item('BOB.LootGroup.Start',group)),
        g.control('control_repeat',g.item('BOB.LootGroup.Count',group),[
            put('BOB.Loot.State',j,1),put('BOB.Loot.Age',j,0),put('BOB.Loot.VelY',j,.2),
            put('BOB.Loot.Y',j,g.op('add',g.item('BOB.Loot.HomeY',j),.5)),
            *[put('BOB.Loot.'+a,j,g.op('add',g.item('BOB.Loot.Home'+a,j),g.op('multiply',1.5,
                g.math('sin' if a=='X' else 'cos',g.item('BOB.Loot.Yaw',j))))) for a in 'XZ'],g.set(j,1,True)]),
        put('BOB.LootGroup.Triggered',group,1),_broadcast(g,'BOB LOOT RELEASE')]
    g.procedure('BOB | release source coins',[g.control('control_if',g.op('equals',g.item('BOB.LootGroup.Triggered',group),0),spawn)])
    near=lambda prefix,radius:g.op('and',g.op('lt',g.math('abs',g.op('subtract',v('SM64.PosX'),g.item(prefix+'X',i))),radius),
        g.op('and',g.op('lt',g.math('abs',g.op('subtract',v('SM64.PosZ'),g.item(prefix+'Z',i))),radius),
                  g.op('lt',g.math('abs',g.op('subtract',v('SM64.PosY'),g.item(prefix+'Y',i))),3)))
    angle=v('BOB.Post.CurrentAngle',local=True);delta=v('BOB.Post.AngleDelta',local=True)
    posts=[g.control('control_if_else',near('BOB.Post.',4),[
        _call(g,'SM64 MATH | atan2 y %s x %s',[g.op('subtract',v('SM64.PosX'),g.item('BOB.Post.X',i)),
                                                             g.op('subtract',v('SM64.PosZ'),g.item('BOB.Post.Z',i))]),
        g.set(angle,v('Math.Result')),
        g.set(delta,g.op('subtract',g.op('mod',g.op('add',g.op('subtract',angle,g.item('BOB.Post.Angle',i)),540),360),180)),
        g.control('control_if',g.op('gt',g.item('BOB.Post.Timer',i),0),[
            put('BOB.Post.TotalAngle',i,g.op('add',g.item('BOB.Post.TotalAngle',i),delta))]),
        put('BOB.Post.Angle',i,angle),put('BOB.Post.Timer',i,g.op('add',g.item('BOB.Post.Timer',i),1)),
        g.control('control_if',g.op('gt',g.math('abs',g.item('BOB.Post.TotalAngle',i)),1080),[
            g.set(group,i),g.call('BOB | release source coins')]),
        g.control('control_if',g.op('gt',g.item('BOB.Post.Timer',i),199),[
            put('BOB.Post.Timer',i,0),put('BOB.Post.TotalAngle',i,0)])],
        [put('BOB.Post.Timer',i,0),put('BOB.Post.TotalAngle',i,0)]),g.set(i,1,True)]
    attacking=lambda:g.op('or',g.op('equals',v('SM64.Action'),'ACT_PUNCHING'),g.op('or',
        g.op('equals',v('SM64.Action'),'ACT_MOVE_PUNCHING'),g.op('or',g.op('equals',v('SM64.Action'),'ACT_JUMP_KICK'),
        g.op('or',g.op('equals',v('SM64.Action'),'ACT_GROUND_POUND'),g.op('or',g.op('equals',v('SM64.Action'),'ACT_GROUND_POUND_LAND'),
        g.op('or',g.op('equals',v('SM64.Action'),'ACT_DIVE'),g.op('equals',v('SM64.Action'),'ACT_DIVE_SLIDE')))))))
    bombs=[g.control('control_if',g.op('and',near('BOB.Bomb.',4),g.op('equals',g.item('BOB.LootGroup.Triggered',g.op('add',i,8)),0)),[
        put('BOB.Bomb.Fuse',i,g.op('add',g.item('BOB.Bomb.Fuse',i),1)),
        g.control('control_if',g.op('or',attacking(),g.op('gt',g.item('BOB.Bomb.Fuse',i),150)),[
            g.set(group,g.op('add',i,8)),g.call('BOB | release source coins')])]),g.set(i,1,True)]
    g.procedure('BOB | update loot sources',[g.control('control_if',g.op('and',g.op('equals',v('BOB.StarSpawn.Active'),0),
        g.op('equals',v('BOB.Warp.Phase'),0)),[g.set(i,1),g.control('control_repeat',g.length('BOB.Post.X'),posts),
        g.set(i,1),g.control('control_repeat',g.length('BOB.Box.X'),[
            g.control('control_if',g.op('and',near('BOB.Box.',2),attacking()),[
                g.set(group,g.op('add',i,5)),g.call('BOB | release source coins')]),g.set(i,1,True)]),
        g.set(i,1),g.control('control_repeat',g.length('BOB.Bomb.X'),bombs)])])


def _teleports(g):
    g.list('BOB.Warp.Node')
    v=g.var;i=v('BOB.Warp.CheckIndex',local=True);near=lambda:g.op('and',
        g.op('lt',g.math('abs',g.op('subtract',v('SM64.PosX'),g.item('BOB.Warp.X',i))),.85),
        g.op('and',g.op('lt',g.math('abs',g.op('subtract',v('SM64.PosZ'),g.item('BOB.Warp.Z',i))),.85),
        g.op('lt',g.math('abs',g.op('subtract',v('SM64.PosY'),g.item('BOB.Warp.Y',i))),1.5)))
    anynear=v('BOB.Warp.NearAny',local=True)
    idle=lambda:g.op('or',g.op('equals',v('SM64.Action'),'ACT_IDLE'),g.op('or',g.op('equals',v('SM64.Action'),'ACT_PANTING'),
            g.op('or',g.op('equals',v('SM64.Action'),'ACT_CROUCHING'),g.op('equals',v('SM64.Action'),'ACT_STANDING_AGAINST_WALL'))))
    g.procedure('BOB | check local warps',[g.set(anynear,0),g.set(i,1),g.control('control_repeat',g.length('BOB.Warp.X'),[
        g.control('control_if',near(),[g.set(anynear,1),g.control('control_if',g.op('and',idle(),g.op('and',
            g.op('equals',v('BOB.Warp.Lock'),0),g.op('and',g.op('equals',v('BOB.Warp.Phase'),0),g.op('equals',v('BOB.StarSpawn.Active'),0)))),[
            g.set(v('BOB.Warp.DestinationIndex'),g.item('BOB.Warp.Destination',i)),g.set(v('BOB.Warp.Lock'),1),
            g.set(v('BOB.Warp.Phase'),1),g.set(v('BOB.Warp.Timer'),0),
            g.set(v('SM64.ActionGroup'),'CUTSCENE'),_broadcast(g,'BOB TELEPORT')])]),g.set(i,1,True)]),
        g.control('control_if',g.op('and',g.op('equals',anynear,0),g.op('equals',v('BOB.Warp.Phase'),0)),[g.set(v('BOB.Warp.Lock'),0)])])
    dest=v('BOB.Warp.DestinationIndex')
    move=[*[g.set(v('SM64.Pos'+a),g.item('BOB.Warp.'+a,dest)) for a in 'XYZ'],
          g.set(v('SM64.FaceYaw'),g.item('BOB.Warp.Yaw',dest)),g.set(v('BOB.Warp.Phase'),2),g.set(v('BOB.Warp.Timer'),0),
          g.call('BOB | reset camera at Mario'),_broadcast(g,'BOB TELEPORT')]
    g.procedure('BOB | advance local warp',[g.set(v('BOB.Warp.Timer'),1,True),
        *[g.set(v(name),0) for name in ('SM64.ForwardVel','SM64.VelX','SM64.VelY','SM64.VelZ')],
        _call(g,ANIM,['Other Standing Normal Part 1',1,1]),
        g.control('control_if_else',g.op('equals',v('BOB.Warp.Phase'),1),[
            g.control('control_if',g.op('gt',v('BOB.Warp.Timer'),20),move)],
            [g.control('control_if',g.op('gt',v('BOB.Warp.Timer'),31),[
                g.set(v('BOB.Warp.Phase'),0),_call(g,ACTION,['ACT_IDLE',0])])])])


def _reward_camera(g,wg):
    v=g.var
    # Replace the immediate red-star cue with a queued camera request.
    for k,b in wg.blocks.items():
        if b['opcode']=='event_broadcast' and 'BOB RED STAR APPEAR' in str(b['inputs']):
            b.update(opcode='data_setvariableto',inputs={'VALUE':[1,[10,'1']]},fields={'VARIABLE':list(wg.var('BOB.StarSpawn.PendingRed')[1:])})
    start=[]
    for axis in 'XYZ':
        start.extend([g.set(v('BOB.StarSpawn.Saved'+axis),v('cam '+axis.lower())),
                      g.set(v('BOB.StarSpawn.SavedFocus'+axis),v('Camera64.Focus'+axis))])
    for axis in 'xyz':start.append(g.set(v('BOB.StarSpawn.SavedR'+axis),v('cam r'+axis)))
    start.extend([g.set(v('BOB.StarSpawn.SavedInit'),v('CamSM64.Init')),
                  g.set(v('BOB.StarSpawn.SavedYaw'),v('Camera64.Yaw')),
                  g.set(v('BOB.StarSpawn.SavedActionGroup'),v('SM64.ActionGroup')),
                  g.set(v('SM64.ActionGroup'),'CUTSCENE')])
    start.extend([g.set(v('BOB.StarSpawn.Active'),1),g.set(v('BOB.StarSpawn.Timer'),0),g.set(v('SM64.ForwardVel'),0),
        *[g.set(v('SM64.Vel'+a),0) for a in 'XYZ']])
    start.extend([g.set(v('BOB.StarSpawn.Height'),0),
        g.control('control_if_else',g.op('equals',v('BOB.StarSpawn.Kind'),4),
            [g.set(v('BOB.StarSpawn.Home'+a),x) for a,x in zip('XYZ',(-60,10,24))],
            [g.set(v('BOB.StarSpawn.Home'+a),v('BOB.HundredStar.'+a)) for a in 'XYZ'])])
    g.procedure('BOB | start reward camera',start)
    hundred=[g.set(v('BOB.HundredStar.Spawned'),1),g.set(v('BOB.StarSpawn.Pending100'),1),
        *[g.set(v('BOB.HundredStar.'+a),g.op('add',v('SM64.Pos'+a),3 if a=='Y' else 0)) for a in 'XYZ']]
    red=[g.set(v('BOB.StarSpawn.Kind'),4),g.set(v('BOB.StarSpawn.PendingRed'),0),g.call('BOB | start reward camera')]
    blue=[g.set(v('BOB.StarSpawn.Kind'),7),g.set(v('BOB.StarSpawn.Pending100'),0),g.set(v('BOB.HundredStar.Visible'),1),g.call('BOB | start reward camera')]
    g.procedure('BOB | queue reward cameras',[
        g.control('control_if',g.op('and',g.op('gt',v('Coin.Count'),99),g.op('equals',v('BOB.HundredStar.Spawned'),0)),hundred),
        g.control('control_if',g.op('and',g.op('equals',v('BOB.StarSpawn.Active'),0),g.op('and',g.op('equals',v('Star.State'),0),
                      g.op('equals',v('BOB.Warp.Phase'),0))),[
            g.control('control_if_else',g.op('equals',v('BOB.StarSpawn.PendingRed'),1),red,[
                g.control('control_if',g.op('equals',v('BOB.StarSpawn.Pending100'),1),blue)])])])
    _wrap(g,'BOB | queue reward cameras',g.op('or',g.op('equals',v('BOB.Cannon.Active'),1),
        g.op('equals',v('SM64.ActionGroup'),'CUTSCENE')),[g.set(v('BOB.StarSpawn.Deferred'),1)])
    t=v('BOB.StarSpawn.Timer');home=lambda a:v('BOB.StarSpawn.Home'+a)
    # A four-unit rise and fall follows the native star spawn arc. The
    # requested 100-coin camera is an extension; the native star has no exit.
    step=[g.set(t,1,True),_call(g,ANIM,['Other Standing Normal Part 1',1,1]),
        g.control('control_if_else',g.op('equals',v('BOB.StarSpawn.Kind'),4),
            [g.set(home(a),x) for a,x in zip('XYZ',(-60,10,24))],
            [g.set(home(a),v('BOB.HundredStar.'+a)) for a in 'XYZ']),
        g.set(v('BOB.StarSpawn.Height'),g.op('multiply',4,g.math('sin',g.op('multiply',
               g.op('divide',t,100),180)))),
        g.control('control_if',g.op('gt',t,100),[g.set(v('BOB.StarSpawn.Height'),0)]),
        g.control('control_if',g.op('equals',t,1),[_broadcast(g,'BOB RED STAR APPEAR')]),
        g.control('control_if',g.op('gt',t,119),[
            g.set(v('BOB.StarSpawn.Active'),0),g.call('BOB | restore saved reward camera')])]
    g.procedure('BOB | advance reward camera',step)
    restore=[]
    for axis in 'XYZ':restore.extend([g.set(v('cam '+axis.lower()),v('BOB.StarSpawn.Saved'+axis)),
                                     g.set(v('Camera64.Focus'+axis),v('BOB.StarSpawn.SavedFocus'+axis))])
    restore.extend([g.set(v('cam r'+a),v('BOB.StarSpawn.SavedR'+a)) for a in 'xyz'])
    restore.extend([g.set(v('CamSM64.Init'),v('BOB.StarSpawn.SavedInit')),
                    g.set(v('Camera64.Yaw'),v('BOB.StarSpawn.SavedYaw')),
                    g.set(v('SM64.ActionGroup'),v('BOB.StarSpawn.SavedActionGroup'))])
    g.procedure('BOB | restore saved reward camera',restore)
    # Show the location immediately, then hold a stable shot while the star
    # settles. Save/restore prevents the normal controller from inheriting it.
    camera=[]
    for axis,offset in zip('XYZ',(7,5,9)):
        camera.append(g.set(v('cam '+axis.lower()),g.op('add',home(axis),offset)))
        camera.append(g.set(v('Camera64.Focus'+axis),g.op('add',home(axis),1 if axis=='Y' else 0)))
    camera.extend([_call(g,'SM64 MATH | atan2 y %s x %s',[-7,-9]),g.set(v('cam ry'),v('Math.Result')),
        g.set(v('cam rx'),g.math('atan',g.op('divide',-4,g.math('sqrt',130)))),g.set(v('cam rz'),0)])
    _wrap(g,'CAMERA | update isolated',g.op('equals',v('BOB.StarSpawn.Active'),1),camera)
    idx=wg.var('BOB.StarSpawn.SceneIndex',local=True);write=lambda off,val:_put(wg,wg.op('add',idx,off),val)
    body=[wg.control('control_if_else',wg.op('equals',wg.var('BOB.StarSpawn.Kind'),4),
        [wg.set(idx,wg.var('BOB.RedStar.SceneIndex'))],[wg.set(idx,wg.var('BOB.HundredStar.SceneIndex'))]),
        write(4,'3d'),*[write(off,wg.var('BOB.StarSpawn.Home'+a)) for off,a in [(10,'X'),(14,'Z')]],
        write(12,wg.op('add',wg.var('BOB.StarSpawn.HomeY'),wg.var('BOB.StarSpawn.Height'))),
        write(18,wg.op('multiply',wg.var('BOB.StarSpawn.Timer'),22.5))]
    wg.procedure('WORLD | sync reward star flight',[wg.control('control_if',wg.op('equals',wg.var('BOB.StarSpawn.Active'),1),body)])
    _hundred_collection(g)
    _wrap(g,'SM64 STAR | check collection',g.op('or',g.op('equals',v('BOB.StarSpawn.Active'),1),
        g.op('gt',v('BOB.Warp.Phase'),0)),[g.set(v('SM64.ForwardVel'),0)])


def _hundred_collection(g):
    v=g.var
    g.procedure('BOB | collect hundred-coin star',[g.control('control_if',g.op('and',g.op('equals',v('BOB.HundredStar.Visible'),1),
        g.op('and',g.op('equals',v('BOB.StarSpawn.Active'),0),g.op('and',g.op('equals',v('Star.State'),0),
        g.op('and',g.op('lt',g.math('abs',g.op('subtract',v('SM64.PosX'),v('BOB.HundredStar.X'))),1.1),g.op('and',
        g.op('lt',g.math('abs',g.op('subtract',v('SM64.PosY'),v('BOB.HundredStar.Y'))),2.2),
        g.op('lt',g.math('abs',g.op('subtract',v('SM64.PosZ'),v('BOB.HundredStar.Z'))),1.1)))))),[
            g.set(v('BOB.HundredStar.Visible'),0),g.set(v('BOB.Star.Kind'),7),g.set(v('Star.SceneName'),HUNDRED_STAR),
            g.call('BOB STAR | collect selected objective')])])
    _wrap(g,'BOB | collect hundred-coin star',g.op('or',g.op('gt',v('BOB.Warp.Phase'),0),
        g.op('or',g.op('equals',v('BOB.Cannon.Active'),1),g.op('equals',v('SM64.ActionGroup'),'CUTSCENE'))),
        [g.set(v('BOB.HundredStar.PickupDeferred'),1)])
    definition=_definition(g,'BOB STAR | collect selected objective');old=g.blocks[definition]['next'];following=g.blocks[old]['next']
    own=g.control('control_if_else',g.op('equals',v('BOB.Star.Kind'),7),[
        g.control('control_if',g.op('equals',v('Star.BOB7Collected'),0),[
            g.set(v('Star.BOB7Collected'),1),g.set(v('Star.Count'),1,True)])],[])
    g.blocks[own]['inputs']['SUBSTACK2']=[2,old];g.blocks[old]['parent']=own;g.blocks[old]['next']=None
    g.blocks[own]['next']=following;g.blocks[following]['parent']=own
    g.blocks[definition]['next']=own;g.blocks[own]['parent']=definition
    dance=[g.set(v('SM64.ForwardVel'),0),g.set(v('SM64.PosY'),v('SM64.FloorHeight')),
        _call(g,ANIM,['Other Get star',0,1]),g.call('SM64 STAR | update celebration star'),
        g.control('control_if',g.op('gt',v('SM64.ActionTimer'),59),[
            _put(g,g.op('add',v('BOB.HundredStar.SceneIndex'),4),'hidden'),g.set(v('Star.State'),0),
            g.set(v('Star.ModelDirty'),0),g.set(v('Star.SceneName'),STAR),_call(g,ACTION,['ACT_IDLE',0]),
            _broadcast(g,'BOB HUNDRED STAR RESUME')])]
    _wrap(g,'SM64 STAR | update dance',g.op('equals',v('BOB.Star.Kind'),7),dance)


def _camera(g):
    v=g.var
    # Keep radial tracking and wall avoidance; remove floor-triggered heading
    # overrides that conflict with player rotation on the mountain. There is
    # no King Bob-omb fight to justify the summit's boss-camera override.
    types=('SURFACE_BOSS_FIGHT_CAMERA','SURFACE_CAMERA_ROTATE_LEFT',
           'SURFACE_CAMERA_ROTATE_RIGHT','SURFACE_CAMERA_MIDDLE')
    match=g.op('equals',v('CamSM64.MarioFloorType'),types[0])
    for name in types[1:]:match=g.op('or',match,g.op('equals',v('CamSM64.MarioFloorType'),name))
    g.insert_after('v084_b_0004',[g.control('control_if',g.op('and',g.op('equals',v('Level.ID'),'BOB'),match),
        [g.set(v('CamSM64.MarioFloorType'),'SURFACE_DEFAULT')])])
    # Local warps seed a complete behind-Mario camera before revealing the destination.
    body=[]
    for axis,offset in zip('XYZ',(0,4,0)):
        body.append(g.set(v('Camera64.Focus'+axis),g.op('add',v('SM64.Pos'+axis),1.25 if axis=='Y' else 0)))
        if axis=='Y':position=g.op('add',v('SM64.PosY'),4)
        else:position=g.op('subtract',v('SM64.Pos'+axis),g.op('multiply',10,
                                g.math('sin' if axis=='X' else 'cos',v('SM64.FaceYaw'))))
        body.append(g.set(v('cam '+axis.lower()),position))
    body.extend([g.set(v('cam ry'),v('SM64.FaceYaw')),g.set(v('cam rx'),-15.376),
        g.set(v('cam rz'),0),g.set(v('Camera64.Yaw'),g.op('mod',g.op('add',v('SM64.FaceYaw'),180),360)),
        g.set(v('CamSM64.Init'),0),g.set(v('CamSM64.TransitionFrames'),0),
        g.set(v('CamSM64.SnapLakitu'),1),g.set(v('Camera64.JustInherited'),0)])
    g.procedure('BOB | reset camera at Mario',body)


def _teleport_overlay(project):
    from sm64_scratch.patches.patch_game_features import _sprite
    sprite,stage,g=_sprite(project,'BOB Teleport Fade');sprite['layerOrder']=122
    sprite.update(x=0,y=0,size=100,visible=False)
    sprite['costumes'][0].update(name='White',rotationCenterX=240,rotationCenterY=180)
    ghost=g.var('BOB.Warp.Ghost',local=True)
    show=[g.control('control_if_else',g.op('equals',g.var('BOB.Warp.Phase'),1),
        [g.set(ghost,g.op('subtract',100,g.op('multiply',g.var('BOB.Warp.Timer'),100/21)))],
        [g.set(ghost,g.op('multiply',g.var('BOB.Warp.Timer'),100/32))]),
        g.node('looks_seteffectto',{'VALUE':ghost},{'EFFECT':['GHOST',None]}),g.node('looks_show')]
    g.hat('event_whenbroadcastreceived',[g.control('control_if_else',g.op('gt',g.var('BOB.Warp.Phase'),0),show,[g.node('looks_hide')])],
          ['ENGINE FRAME RENDERED','porter_frame_rendered'])
    g.hat('event_whenflagclicked',[g.node('looks_hide')])


def build_bob_overlay_assets():
    return {'BOB Teleport Fade':[('White',b'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="360"><path fill="white" d="M0 0h480v360H0z"/></svg>')]}
