"""Bounded whole-report repair. Re-detect IDs between every topology edit."""
from dataclasses import dataclass,replace
import uuid
from .engine import Inspector
from .model import RULE_MAP
from .maya_bridge import _host,snapshot
from .repair import plan_edit,apply_edit,TITLES

STAGES=('duplicate_face','duplicate_vertex','zero_edge','degenerate','internal',
        'intersection','nonmanifold','t_junction','sliver','boundary','ngon','concave','normals','duplicate_face')
DELETION={'degenerate','internal','intersection','nonmanifold','t_junction','sliver'}

@dataclass(frozen=True)
class BulkOptions:
    fill:bool=True
    normals:bool=True
    delete:bool=False
    triangulate:bool=False

def action_for(rule,options):
    if rule in ('ngon','concave'): return 'triangulate' if options.triangulate else None
    if rule=='duplicate_face': return 'deduplicate'
    if rule=='duplicate_vertex': return 'merge'
    if rule=='zero_edge': return 'collapse'
    if rule=='boundary' and options.fill: return 'fill'
    if rule=='normals' and options.normals: return 'normals'
    if rule in DELETION and options.delete: return 'delete'
    return None

def redundant_faces(mesh,ids):
    """Exact simple-face duplicates only; keep one face for every cyclic key.

    Tolerance-only matches/holed faces need individual decisions and remain
    reported. Never remove every member of a duplicate group.
    """
    seen=set(); remove=[]
    for f in sorted(ids):
        if f in mesh.holed_faces: continue
        points=tuple(mesh.points[v] for v in mesh.faces[f])
        if not points or len(set(points))!=len(points): continue
        reverse=points[::-1]
        # Unique coordinates imply a unique smallest start point: O(n),
        # without allocating all cyclic rotations of a very large N-gon.
        start=min(points); i=points.index(start); j=reverse.index(start)
        key=min(points[i:]+points[:i],reverse[j:]+reverse[:j])
        if key in seen: remove.append(f)
        else: seen.add(key)
    return remove

def repair_all(report,options=BulkOptions(),progress=None):
    cmds,om=_host()
    if report.cancelled or not report.complete:
        raise RuntimeError('未完了の検査があります。検査を完了してから一括修復してください。')
    if not cmds.undoInfo(query=True,state=True):
        raise RuntimeError('MayaのUndoを有効にしてください。')
    targets=[r for r in report.meshes if r.findings]
    # Preflight every target before the first edit. Never partially start a
    # batch using stale IDs, shared instance geometry or read-only shapes.
    for result in targets:
        mesh=snapshot(result.path)
        if mesh.fingerprint!=result.fingerprint:
            raise RuntimeError('検査後に形状が変わっています。再検査してください: '+result.path)
        sl=om.MSelectionList(); sl.add(result.path); dag=sl.getDagPath(0)
        if len(om.MDagPath.getAllPathsTo(dag.node()))>1:
            raise RuntimeError('共有インスタンスを含むため一括修復できません: '+result.path)
        if cmds.referenceQuery(result.path,isNodeReferenced=True) or any(cmds.lockNode(result.path,q=True,lock=True)):
            raise RuntimeError('参照・ロックされた対象を含んでいます: '+result.path)
    token='MeshSentinel_All_'+uuid.uuid4().hex
    changed=set(); log=[]
    cmds.undoInfo(openChunk=True,chunkName=token)
    try:
        for target in targets:
            for rule in STAGES:
                action=action_for(rule,options)
                if rule not in report.settings.enabled: continue
                if not action:
                    if any(f.rule==rule for f in target.findings):
                        log.append(target.path+' / '+RULE_MAP[rule].title+': 個別確認として残しました')
                    continue
                if progress: progress(target.path,RULE_MAP[rule].title)
                # Mesh IDs can be renumbered after merge/delete/triangulate.
                # Analyze only this stage's rule, from a fresh native snapshot.
                mesh=snapshot(target.path)
                result=Inspector(replace(report.settings,enabled=(rule,))).inspect(mesh)
                if result.checks.get(rule)!='complete':
                    log.append(target.path+' / '+RULE_MAP[rule].title+': 再検査が未完了のため変更しません')
                    continue
                # One finding at a time, then move on: no old IDs may be used
                # after the first command in this stage (e.g. e + vtx groups).
                finding=next((f for f in result.findings if f.ids),None)
                if not finding: continue
                ids=list(finding.ids)
                if action=='deduplicate':
                    ids=redundant_faces(mesh,ids); action='delete'
                    if not ids:
                        log.append(target.path+' / 重複面: 許容距離内の候補は個別確認として残しました')
                        continue
                if action=='normals':
                    opposing=set(mesh.opposing_normals)
                    ids=[i for i in ids if i in opposing] or ids
                    action='reset_normals' if set(ids).issubset(opposing) else 'reverse'
                if action=='fill' and finding.component=='f':
                    if not options.triangulate:
                        log.append(target.path+' / 穴付き面: 三角化がオフのため個別確認として残しました')
                        continue
                    action='triangulate'
                try:
                    plan=plan_edit(finding,ids,action,mesh,report.settings)
                except ValueError as exc:
                    log.append(target.path+' / '+RULE_MAP[rule].title+': '+str(exc)); continue
                if action=='delete' and len(plan.ids)==len(mesh.faces):
                    log.append(target.path+' / '+RULE_MAP[rule].title+': 全面削除になるため個別確認として残しました')
                    continue
                apply_edit(plan,manage_undo=False)
                if snapshot(target.path).fingerprint!=mesh.fingerprint:
                    changed.add(target.path)
                    log.append(target.path+' / '+TITLES[action]+': {} 箇所'.format(len(plan.ids)))
        # Finite pass; residual/cyclic problems are shown by the final review,
        # never hidden or declared fixed just because an operation was attempted.
    except Exception:
        cmds.undoInfo(closeChunk=True)
        if cmds.undoInfo(q=True,undoName=True)==token: cmds.undo()
        raise
    else:
        cmds.undoInfo(closeChunk=True)
    return sorted(changed),log
