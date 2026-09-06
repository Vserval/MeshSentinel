"""Explicit component edits with snapshot preflight and one Undo per action."""
from dataclasses import dataclass
import uuid
import math
import itertools
from .model import Finding
from .maya_bridge import _host,snapshot

TITLES={'triangulate':'選択面を三角形化','merge':'重複頂点をマージ',
        'collapse':'ゼロ長エッジを縮約','reverse':'選択面の法線を反転',
        'reset_normals':'選択面のカスタム法線を面法線へ戻す',
        'fill':'選択エッジを含む穴を塞ぐ','delete':'選択箇所の面を削除'}

def actions_for(finding):
    result=[]
    if finding.rule in ('ngon','concave') or (finding.rule=='boundary' and finding.component=='f'):
        result.append('triangulate')
    if finding.rule=='duplicate_vertex': result.append('merge')
    if finding.rule=='zero_edge': result.append('collapse')
    if finding.rule=='normals': result+=['reverse','reset_normals']
    if finding.rule=='boundary' and finding.component=='e': result.append('fill')
    return result+['delete']

@dataclass(frozen=True)
class EditPlan:
    path:str
    fingerprint:str
    rule:str
    action:str
    kind:str
    ids:tuple
    distance:float
    explanation:str

    @property
    def components(self):
        return ['{}.{}[{}]'.format(self.path,self.kind,i) for i in self.ids]

def checked_mesh(finding,results):
    current=snapshot(finding.mesh)
    expected=next((r for r in results if r.path==finding.mesh),None)
    if expected is None or current.fingerprint!=expected.fingerprint:
        raise RuntimeError('検査後に形状が変わりました。再検査の完了後に開き直してください。')
    return current

def plan_edit(finding,ids,action,mesh,settings):
    ids=set(ids)
    if not ids or not ids.issubset(finding.ids) or action not in actions_for(finding):
        raise ValueError('この検出結果のコンポーネントと操作を選択してください。')
    if mesh.path!=finding.mesh:
        raise ValueError('対象メッシュが一致しません。')
    kind=finding.component
    limit=len({'f':mesh.faces,'e':mesh.edges,'vtx':mesh.points}[kind])
    if min(ids)<0 or max(ids)>=limit: raise ValueError('古いコンポーネントIDです。')
    explanation='選択したコンポーネントだけを変更します。'
    if action=='delete':
        if kind=='vtx':
            ids={f for f,vs in enumerate(mesh.faces) if ids.intersection(vs)}
        elif kind=='e':
            if mesh.edge_incidence:
                ids={entry[0] for i in ids for entry in mesh.edge_incidence[i]}
            else:
                edges={tuple(sorted(mesh.edges[i])) for i in ids}
                ids={f for f,vs in enumerate(mesh.faces)
                     if any(tuple(sorted((a,b))) in edges for a,b in zip(vs,vs[1:]+vs[:1]))}
        kind='f'
        explanation='以下の面を削除します。エッジ／頂点を指定した場合は接続面が対象です。開口が生じます。'
        if finding.rule=='duplicate_face':
            explanation+=' 重複面のうち残したい面は選ばないでください。'
        if len(ids)==len(mesh.faces):
            explanation+=' 全フェイスが対象のため、このメッシュ形状全体を削除します。'
    elif action=='merge':
        # Include only detected partners of explicitly selected vertices.
        eps=settings.distance; eps2=eps**2
        grid={}
        for j in ids:
            cell=tuple(math.floor(x/eps) for x in mesh.points[j])
            grid.setdefault(cell,[]).append(mesh.points[j])
        for i in finding.ids:
            if i in ids: continue
            cell=tuple(math.floor(x/eps) for x in mesh.points[i])
            nearby=(p for offset in itertools.product((-1,0,1),repeat=3)
                    for p in grid.get(tuple(a+b for a,b in zip(cell,offset)),()))
            if any(sum((a-b)**2 for a,b in zip(mesh.points[i],p))<=eps2 for p in nearby):
                ids.add(i)
        if len(ids)<2: raise ValueError('許容距離内の重複相手が見つかりません。')
        explanation='選択頂点と検出済みの重複相手を、距離 {} cm 以内でマージします。UVシームは維持します。接続と位置が変わります。'.format(settings.distance)
    elif action=='fill':
        # Expand the complete boundary explicitly: Maya closes the whole loop
        # even when given one edge. Refuse branching/non-manifold boundaries.
        edge_ids={tuple(sorted(e)):i for i,e in enumerate(mesh.edges)}
        incidence=[len(entries) for entries in mesh.edge_incidence] if mesh.edge_incidence else [0]*len(mesh.edges)
        if not mesh.edge_incidence:
            for vs in mesh.faces:
                for a,b in zip(vs,vs[1:]+vs[:1]):
                    i=edge_ids.get(tuple(sorted((a,b))))
                    if i is not None: incidence[i]+=1
        boundary={i for i,n in enumerate(incidence) if n==1}
        if not ids.issubset(boundary): raise ValueError('境界エッジ以外を含んでいます。')
        by_vertex={}
        for i in boundary:
            for v in mesh.edges[i]: by_vertex.setdefault(v,set()).add(i)
        todo=list(ids)
        while todo:
            i=todo.pop()
            for v in mesh.edges[i]:
                if len(by_vertex[v])!=2: raise ValueError('分岐した境界です。非多様体を先に修正してください。')
                for j in by_vertex[v]-ids: ids.add(j); todo.append(j)
        explanation='選択したエッジを含む境界ループ全体を塞ぎます。以下は拡張後の全エッジです。生成面がN-gonになる場合があります。'
    elif action=='reverse':
        explanation='選択面の巻き順と法線を反転します。正しい表裏は形状の意図に合わせて選んでください。'
    elif action=='reset_normals':
        explanation='選択面のカスタム法線を解除して面法線に置き換えます。陰影が変わります。巻き順は変えません。'
    elif action=='triangulate':
        explanation='選択面をMayaの三角形分割で置き換えます。外周を保ち、面IDと内部エッジが変わります。'
    elif action=='collapse':
        explanation='選択エッジの両端を縮約します。接続する面の形状・頂点数が変わります。'
    if not ids: raise ValueError('操作できる面がありません。')
    return EditPlan(mesh.path,mesh.fingerprint,finding.rule,action,kind,tuple(sorted(ids)),settings.distance,explanation)

def apply_edit(plan,manage_undo=True):
    cmds,om=_host()
    current=snapshot(plan.path)
    if current.fingerprint!=plan.fingerprint:
        raise RuntimeError('プレビュー後に形状が変わりました。再検査して開き直してください。')
    sl=om.MSelectionList(); sl.add(plan.path); dag=sl.getDagPath(0)
    if len(om.MDagPath.getAllPathsTo(dag.node()))>1:
        raise RuntimeError('共有形状のインスタンスです。個別編集には先にインスタンスを複製へ変換してください。')
    if cmds.referenceQuery(plan.path,isNodeReferenced=True) or any(cmds.lockNode(plan.path,query=True,lock=True)):
        raise RuntimeError('参照またはロックされたメッシュは編集できません。')
    if not cmds.undoInfo(query=True,state=True):
        raise RuntimeError('MayaのUndoを有効にしてから実行してください。')
    token='MeshSentinel_'+uuid.uuid4().hex
    if manage_undo: cmds.undoInfo(openChunk=True,chunkName=token)
    try:
        c=plan.components
        if plan.action=='triangulate': cmds.polyTriangulate(c,constructionHistory=True)
        elif plan.action=='merge':
            cmds.polyMergeVertex(c,distance=str(plan.distance)+'cm',worldSpace=True,
                                alwaysMergeTwoVertices=False,texture=False,constructionHistory=True)
        elif plan.action=='collapse': cmds.polyCollapseEdge(c,constructionHistory=True)
        elif plan.action=='reverse': cmds.polyNormal(c,normalMode=0,userNormalMode=True,constructionHistory=True)
        elif plan.action=='reset_normals': cmds.polySetToFaceNormal(c,setUserNormal=True)
        elif plan.action=='fill': cmds.polyCloseBorder(c,constructionHistory=True)
        elif plan.action=='delete':
            # Maya may leave a single-face mesh unchanged when deleting its
            # last component. Explicit full-face scope removes the shape node.
            cmds.delete(plan.path if len(plan.ids)==len(current.faces) else c)
        else: raise ValueError('不明な操作です。')
    except Exception:
        if not manage_undo: raise
        cmds.undoInfo(closeChunk=True)
        # Never undo unrelated user work if the failed command made no entry.
        if cmds.undoInfo(query=True,undoName=True)==token: cmds.undo()
        raise
    else:
        if manage_undo: cmds.undoInfo(closeChunk=True)
    return plan.path
