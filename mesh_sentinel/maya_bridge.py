"""All host API access is restricted to Maya's main thread."""
from dataclasses import replace
import math
import threading

from .model import MeshData
from .geometry import normal, dot, length, add


def _host():
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Maya API access must run on the main thread")
    return cmds, om


def mesh_paths(scope="selected"):
    cmds, om = _host()
    if scope == "scene":
        paths = cmds.ls(dag=True, type="mesh", long=True, allPaths=True) or []
    else:
        roots = cmds.ls(selection=True, objectsOnly=True, long=True) or []
        paths = []
        for root in roots:
            candidates = cmds.ls(root, dag=True, type="mesh", long=True, allPaths=True) or []
            paths.extend(p for p in candidates if p == root or p.startswith(root + "|"))
    return sorted({p for p in paths if not cmds.getAttr(p + ".intermediateObject")})


def snapshot(path):
    cmds, om = _host()
    selection = om.MSelectionList()
    selection.add(path)
    dag = selection.getDagPath(0)
    fn = om.MFnMesh(dag)
    points = tuple((p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld))
    if any(not math.isfinite(x) for p in points for x in p):
        raise ValueError("非有限の頂点座標が含まれています: " + path)
    faces, triangles, concave, holed, invalid, opposing = [], [], [], [], [], []
    iterator = om.MItMeshPolygon(dag)
    while not iterator.isDone():
        f = iterator.index()
        vs = tuple(iterator.getVertices())
        faces.append(vs)
        if iterator.isHoled():
            holed.append(f)
        if len(vs) >= 5 and not iterator.isConvex():
            concave.append(f)
        valid = iterator.hasValidTriangulation()
        if not valid:
            invalid.append(f)
        else:
            _, ids = iterator.getTriangles(om.MSpace.kWorld)
            face_tris = [tuple(ids[i:i+3]) for i in range(0, len(ids), 3)]
            triangles.extend((f,) + t for t in face_tris)
            geometric = (0., 0., 0.)
            for t in face_tris:
                geometric = add(geometric, normal([points[v] for v in t]))
            gl = length(geometric)
            if gl > 0:
                for n in iterator.getNormals(om.MSpace.kWorld):
                    nv = (n.x, n.y, n.z)
                    if dot(geometric, nv) < -1e-6 * gl * length(nv):
                        opposing.append(f)
                        break
        iterator.next()
    # A holed polygon's flattened vertex list is not a single boundary loop.
    # Recover its edge directions from Maya's valid triangulation instead.
    directed = {}
    if holed:
        for f, vs in enumerate(faces):
            for a, b in zip(vs, vs[1:] + vs[:1]):
                directed[(f, min(a,b), max(a,b))] = (a,b)
        for f, a, b, c in triangles:
            if f in holed:
                for x, y in ((a,b),(b,c),(c,a)):
                    directed[(f, min(x,y), max(x,y))] = (x,y)
    edges, incidence = [], []
    iterator = om.MItMeshEdge(dag)
    while not iterator.isDone():
        a, b = iterator.vertexId(0), iterator.vertexId(1)
        edges.append((a, b))
        if holed:
            incidence.append(tuple((f,) + directed.get((f,min(a,b),max(a,b)), (a,b)) for f in iterator.getConnectedFaces()))
        iterator.next()
    mesh = MeshData(dag.fullPathName(), fn.uuid().asString(), points, tuple(faces), tuple(edges),
                    tuple(triangles), tuple(concave), tuple(holed), tuple(invalid), tuple(opposing))
    mesh = replace(mesh, edge_incidence=tuple(incidence))
    return replace(mesh, fingerprint=mesh.signature())


def select_findings(findings, results, frame=False):
    """Validate every snapshot before changing the selection; fail atomically."""
    cmds, om = _host()
    paths = {f.mesh for f in findings}
    by_path = {r.path: r for r in results}
    dags = {}
    for path in paths:
        if not cmds.objExists(path):
            raise RuntimeError("対象が削除・リネームされています。再検査してください。")
        current = snapshot(path)
        if path not in by_path or current.fingerprint != by_path[path].fingerprint:
            raise RuntimeError("検査後に形状・変換・法線が変わっています。再検査してください。")
        sl = om.MSelectionList()
        sl.add(path)
        dags[path] = sl.getDagPath(0)
    grouped = {}
    for finding in findings:
        grouped.setdefault((finding.mesh, finding.component), set()).update(finding.ids)
    sl = om.MSelectionList()
    kinds = {"f": om.MFn.kMeshPolygonComponent, "e": om.MFn.kMeshEdgeComponent,
             "vtx": om.MFn.kMeshVertComponent}
    for (path, kind), ids in grouped.items():
        limit={"f":om.MFnMesh(dags[path]).numPolygons,"e":om.MFnMesh(dags[path]).numEdges,
               "vtx":om.MFnMesh(dags[path]).numVertices}[kind]
        if not ids or min(ids)<0 or max(ids)>=limit:
            raise RuntimeError("コンポーネントIDが無効です。再検査してください。")
        fn = om.MFnSingleIndexedComponent()
        comp = fn.create(kinds[kind])
        fn.addElements(sorted(ids))
        sl.add((dags[path], comp))
    if not grouped:
        raise RuntimeError("結果一覧で問題の行を選択してください。")
    # An API active list alone does not switch object mode or a mismatched
    # component mask. Maya then holds an invisible/non-editable selection.
    parents=[]
    for dag in dags.values():
        parent=om.MDagPath(dag); parent.pop(); parents.append(parent.fullPathName())
    cmds.selectMode(component=True)
    cmds.hilite(parents,replace=True)
    cmds.selectType(allComponents=False)
    kinds_present={kind for path,kind in grouped}
    cmds.selectType(polymeshFace='f' in kinds_present,polymeshEdge='e' in kinds_present,
                    polymeshVertex='vtx' in kinds_present)
    if len(kinds_present)>1:
        # Maya's F7 / SelectMultiComponentMask uses meshComponents. Its
        # individual face/edge flags are false while this combined mode is on.
        cmds.selectType(meshComponents=True)
    cmds.select(sl.getSelectionStrings(),replace=True)
    if frame:
        panels=[cmds.getPanel(withFocus=True),cmds.getPanel(underPointer=True)]
        panels+=cmds.getPanel(visiblePanels=True) or []
        panel=next((p for p in panels if p and cmds.getPanel(typeOf=p)=='modelPanel'),None)
        if not panel:
            raise RuntimeError("問題箇所は選択しました。フォーカスするビューポートを表示してください。")
        camera=cmds.modelPanel(panel,query=True,camera=True)
        cmds.viewFit(camera,animate=False,fitFactor=.85)
        cmds.setFocus(panel)
    return sum(len(ids) for ids in grouped.values())


def install_shelf():
    import os
    import maya.mel as mel
    cmds, _ = _host()
    shelf = cmds.tabLayout(mel.eval("$temp=$gShelfTopLevel"), query=True, selectTab=True)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    command = "import sys\np = {!r}\nif p not in sys.path: sys.path.insert(0, p)\nimport mesh_sentinel\nmesh_sentinel.show()".format(root)
    for child in cmds.shelfLayout(shelf, query=True, childArray=True) or []:
        if cmds.objectTypeUI(child) == "shelfButton" and cmds.shelfButton(child, query=True, annotation=True) == "Mesh Sentinel — Mesh Inspector":
            cmds.shelfButton(child, edit=True, command=command)
            return
    cmds.shelfButton(parent=shelf, label="Sentinel", annotation="Mesh Sentinel — Mesh Inspector",
                     sourceType="python", command=command, image="polyCleanup.png",
                     imageOverlayLabel="MS")
