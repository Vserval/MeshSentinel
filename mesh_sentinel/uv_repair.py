"""Unfold preparation without triangulating, deleting or moving polygons."""
from dataclasses import dataclass
import hashlib
import math
import re
import uuid
from .maya_bridge import _host
from .uv_topology import analyze_uv_topology

@dataclass(frozen=True)
class UVState:
    path:str
    fingerprint:str
    surface:str
    uv_set:str
    vertices:int
    face_sizes:tuple
    uv_count:int
    missing_faces:tuple
    nonmanifold_vertices:tuple
    nonmanifold_edges:tuple
    nonmanifold_uvs:tuple
    nonmanifold_uv_edges:tuple

def _ids(path,flag,kind):
    cmds,_=_host()
    found=set()
    for item in cmds.polyInfo(path,**{flag:True}) or []:
        match=re.search(r'\.'+kind+r'\[(\d+)(?::(\d+))?\]$',item.strip())
        if match:
            a=int(match.group(1)); b=int(match.group(2) or a)
            found.update(range(a,b+1))
        else:
            raise RuntimeError('UV診断のコンポーネント形式を読み取れません: '+item)
    return tuple(sorted(found))

def _connectivity_uvs(dag, uv_set):
    """Independent diagnostic; polyInfo may miss disconnected corner fans."""
    _,om=_host()
    it=om.MItMeshPolygon(dag); faces=[]; assignments=[]
    while not it.isDone():
        # Maya flattens holed faces into multiple loops. Do not misinterpret
        # this as one polygon; leave such meshes to the native diagnostics.
        if it.isHoled(): return ()
        vertices=tuple(it.getVertices()); faces.append(vertices)
        try: assignments.append(tuple(it.getUVIndex(i,uv_set) for i in range(len(vertices))))
        except RuntimeError: assignments.append(())
        it.next()
    return analyze_uv_topology(faces,assignments).nonmanifold_uvs


def _corner_uvs(path):
    """Snapshot mapped corner coordinates in every UV set, independent of IDs."""
    _,om=_host()
    sl=om.MSelectionList(); sl.add(path); fn=om.MFnMesh(sl.getDagPath(0))
    data={}
    for name in fn.getUVSetNames():
        u,v=fn.getUVs(name); counts,ids=fn.getAssignedUVs(name); offset=0
        for face,count in enumerate(counts):
            for corner in range(count):
                uv=ids[offset+corner]
                data[(name,face,corner)]=(u[uv],v[uv])
            offset+=count
    return data


def _check_coordinates(before,after):
    for key,point in before.items():
        actual=after.get(key)
        if actual is None or any(not math.isclose(a,b,rel_tol=1e-6,abs_tol=1e-6) for a,b in zip(point,actual)):
            raise RuntimeError('既存UV座標が変わったため処理を戻します: '+str(key))


def _topology_errors(path):
    cmds,_=_host()
    errors=cmds.u3dTopoValid(path+'.map[*]',type=True)
    if not errors: return ()
    if isinstance(errors,str): return (errors,)
    return tuple(str(error) for error in errors)


def inspect_uv(path):
    cmds,om=_host()
    sl=om.MSelectionList(); sl.add(path); dag=sl.getDagPath(0); fn=om.MFnMesh(dag)
    points=tuple(tuple(p)[:3] for p in fn.getPoints(om.MSpace.kWorld))
    counts,vertices=fn.getVertices(); counts=tuple(counts); vertices=tuple(vertices)
    uv_sets=tuple(fn.getUVSetNames())
    current=fn.currentUVSetName() if uv_sets else 'map1'
    uv_data=tuple((name,tuple(map(tuple,fn.getUVs(name))),tuple(map(tuple,fn.getAssignedUVs(name)))) for name in uv_sets)
    signature=(fn.uuid().asString(),points,counts,vertices,current,uv_data)
    fingerprint=hashlib.sha256(repr(signature).encode()).hexdigest()
    surface=hashlib.sha256(repr((counts,tuple(points[v] for v in vertices))).encode()).hexdigest()
    mapped=tuple(fn.getAssignedUVs(current)[0]) if uv_sets else (0,)*len(counts)
    missing=tuple(i for i,count in enumerate(counts) if i>=len(mapped) or mapped[i]!=count)
    return UVState(dag.fullPathName(),fingerprint,surface,current,len(points),counts,
        fn.numUVs(current) if uv_sets else 0,missing,
        _ids(path,'nonManifoldVertices','vtx'),_ids(path,'nonManifoldEdges','e'),
        tuple(sorted(set(_ids(path,'nonManifoldUVs','map')) | set(_connectivity_uvs(dag,current) if uv_sets else ()))),
        _ids(path,'nonManifoldUVEdges','e'))

def _uv_cut_edges(state):
    _,om=_host()
    bad=set(state.nonmanifold_uvs); edges=set(state.nonmanifold_uv_edges)
    if not bad: return sorted(edges)
    sl=om.MSelectionList(); sl.add(state.path); it=om.MItMeshPolygon(sl.getDagPath(0))
    while not it.isDone():
        face_edges=tuple(it.getEdges())
        for corner in range(it.polygonVertexCount()):
            try: uv=it.getUVIndex(corner,state.uv_set)
            except RuntimeError: continue
            if uv in bad:
                edges.update((face_edges[corner],face_edges[(corner-1)%len(face_edges)]))
        it.next()
    return sorted(edges)

def _separate_shared_uv_faces(state):
    """Reassign malformed UV sharing, preserving every face-corner UV position.

    Native edge cuts cannot separate a UV shared across disconnected vertices.
    Rebuild only the affected face assignments to get new IDs, then restore their
    original coordinates with undoable commands. No geometry edit is involved.
    """
    cmds,om=_host()
    sl=om.MSelectionList(); sl.add(state.path); fn=om.MFnMesh(sl.getDagPath(0))
    u,v=fn.getUVs(state.uv_set); bad=set(state.nonmanifold_uvs); corners={}
    for face,size in enumerate(state.face_sizes):
        ids=[fn.getPolygonUVid(face,j,state.uv_set) for j in range(size)]
        if bad.intersection(ids): corners[face]=[(u[i],v[i]) for i in ids]
    if not corners: return
    faces=['{}.f[{}]'.format(state.path,i) for i in corners]
    cmds.polyMapDel(faces,constructionHistory=True)
    cmds.polyProjection(faces,type='Planar',mapDirection='z',constructionHistory=True,uvSetName=state.uv_set)
    # Cut all boundaries of rebuilt faces before restoring potentially different
    # UV positions at opposite corners of a shared geometric vertex.
    edges=cmds.polyListComponentConversion(faces,fromFace=True,toEdge=True)
    cmds.polyMapCut(edges,constructionHistory=True)
    fn=om.MFnMesh(sl.getDagPath(0))
    edits=[]
    for face,coordinates in corners.items():
        for j,(u,v) in enumerate(coordinates):
            edits.append((fn.getPolygonUVid(face,j,state.uv_set),u,v))
    for uv,u,v in edits:
        cmds.polyEditUV('{}.map[{}]'.format(state.path,uv),relative=False,uValue=u,vValue=v,uvSetName=state.uv_set)

def repair_uv(states,create_missing=True,axis='z',split_geometry=True,progress=None,unfold=False):
    """Preserve face count, per-face arity/order and all face-corner positions.

    Geometry seams are needed when planar projection would reproduce the UV
    error from a non-manifold mesh. Existing face-corner UV coordinates stay
    in place when cutting or rebuilding malformed assignments.
    With unfold=True, run Unfold3D afterwards in the same undo transaction;
    only this optional step is allowed to move current-set UV coordinates.
    """
    cmds,om=_host()
    if axis not in ('x','y','z'): raise ValueError('平面投影方向が無効です。')
    if not states: raise ValueError('メッシュを選択してください。')
    if not cmds.undoInfo(q=True,state=True): raise RuntimeError('MayaのUndoを有効にしてください。')
    if not cmds.pluginInfo('Unfold3D',q=True,loaded=True): cmds.loadPlugin('Unfold3D',quiet=True)
    for original in states:
        current=inspect_uv(original.path)
        if current.fingerprint!=original.fingerprint: raise RuntimeError('形状・UVが変わりました。診断を開き直してください。')
        sl=om.MSelectionList(); sl.add(original.path); dag=sl.getDagPath(0)
        if len(om.MDagPath.getAllPathsTo(dag.node()))>1: raise RuntimeError('共有インスタンスは個別の形状へ変換してから実行してください。')
        if cmds.referenceQuery(original.path,isNodeReferenced=True) or any(cmds.lockNode(original.path,q=True,lock=True)):
            raise RuntimeError('参照またはロックされたメッシュは変更できません。')
        if original.missing_faces and not create_missing: raise RuntimeError('UV未割当の面があります。平面投影を許可するか、先に投影してください。')
        if (original.nonmanifold_edges or original.nonmanifold_vertices) and not split_geometry:
            raise RuntimeError('形状自体に非多様体があります。接続を分離しないと再投影時に再発します。')
    token='MeshSentinel_UV_'+uuid.uuid4().hex
    results=[]
    cmds.undoInfo(openChunk=True,chunkName=token)
    try:
        for original in states:
            path=original.path; current=original; split_steps=0; cut_count=0
            coordinates=_corner_uvs(path)
            for step in range(32):
                if not current.nonmanifold_vertices and not current.nonmanifold_edges: break
                if progress: progress(path,'非多様体の接続を分離 ({})'.format(step+1))
                vertices=set(current.nonmanifold_vertices)
                if current.nonmanifold_edges:
                    sl=om.MSelectionList(); sl.add(path); fn=om.MFnMesh(sl.getDagPath(0))
                    for edge in current.nonmanifold_edges: vertices.update(fn.getEdgeVertices(edge))
                cmds.polySplitVertex(['{}.vtx[{}]'.format(path,i) for i in sorted(vertices)],constructionHistory=True)
                updated=inspect_uv(path); split_steps+=1
                if updated.fingerprint==current.fingerprint: break
                current=updated
            if current.nonmanifold_vertices or current.nonmanifold_edges:
                raise RuntimeError('非多様体の接続が残っています。三角化・削除せずに解決できないため変更を戻します。')
            if current.missing_faces:
                cmds.polyProjection(['{}.f[{}]'.format(path,i) for i in current.missing_faces],
                                    type='Planar',mapDirection=axis,uvSetName=current.uv_set,constructionHistory=True)
                current=inspect_uv(path)
            for step in range(8):
                if not current.nonmanifold_uvs and not current.nonmanifold_uv_edges: break
                edges=_uv_cut_edges(current)
                if edges:
                    if progress: progress(path,'非多様体UVの接続をカット')
                    cmds.polyMapCut(['{}.e[{}]'.format(path,i) for i in edges],constructionHistory=True)
                    cut_count+=len(edges)
                updated=inspect_uv(path)
                # UV IDs can also be shared by disconnected geometry; cutting
                # already-open mesh edges cannot separate those UV assignments.
                if updated.nonmanifold_uvs:
                    _separate_shared_uv_faces(updated)
                    updated=inspect_uv(path)
                if updated.fingerprint==current.fingerprint: break
                current=updated
            if current.surface!=original.surface:
                raise RuntimeError('面構成または頂点位置が変わったため、処理を戻します。')
            if current.nonmanifold_uvs or current.nonmanifold_uv_edges or current.missing_faces:
                raise RuntimeError('UVの問題が残ったため処理を戻します。')
            _check_coordinates(coordinates,_corner_uvs(path))
            # This is the same validity checker that Maya's Unfold UI uses.
            errors=_topology_errors(path)
            if errors:
                raise RuntimeError('Unfoldの事前検証が通りません: '+', '.join(errors)+'。変更を戻します。')
            if unfold:
                if progress: progress(path,'修復済みUVをUnfold3Dで展開')
                cmds.u3dUnfold(path+'.map[*]',iterations=1,pack=False)
                current=inspect_uv(path)
                if current.surface!=original.surface or current.missing_faces or current.nonmanifold_uvs or current.nonmanifold_uv_edges or _topology_errors(path):
                    raise RuntimeError('展開後の検証に失敗しました。修復・展開を戻します。')
                _check_coordinates({key:value for key,value in coordinates.items() if key[0]!=original.uv_set},_corner_uvs(path))
            results.append(dict(path=path,before_vertices=original.vertices,after_vertices=current.vertices,
                faces=len(current.face_sizes),quads=current.face_sizes.count(4),split_steps=split_steps,
                uv_cut_edges=cut_count,uv_set=current.uv_set,changed=current.fingerprint!=original.fingerprint,
                surface_preserved=True,unfold_topology_valid=True,unfolded=unfold))
    except Exception:
        cmds.undoInfo(closeChunk=True)
        if cmds.undoInfo(q=True,undoName=True)==token: cmds.undo()
        raise
    else: cmds.undoInfo(closeChunk=True)
    return results

def repair_selected(create_missing=True,axis='z',split_geometry=True,unfold=False):
    """Script Editor entry point; edits the selected meshes in one Undo."""
    from .maya_bridge import mesh_paths
    states=[inspect_uv(path) for path in mesh_paths('selected')]
    return repair_uv(states,create_missing=create_missing,axis=axis,split_geometry=split_geometry,unfold=unfold)


def repair_and_unfold_selected(create_missing=True,axis='z',split_geometry=True):
    """Run AFTER planar projection. Repair + Unfold3D, no triangulation.

    Acts on whole selected meshes (including component selections), current
    UV set only for UV repair/unfold. Non-manifold geometry may be split.
    """
    return repair_selected(create_missing,axis,split_geometry,unfold=True)
