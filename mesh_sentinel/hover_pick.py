"""Read-only, front-surface picking. Never changes Maya's active selection."""
from collections import defaultdict
import html
from .model import RULE_MAP
from .geometry import dot, normal


class HoverIndex:
    def __init__(self, findings, components):
        allowed = set(components)
        self.issues = defaultdict(list)
        for finding in findings:
            for index in finding.ids:
                key = (finding.mesh,finding.component,index)
                if key in allowed:
                    self.issues[key].append(finding)


def scene_meshes(view=None):
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    paths = sorted(set(cmds.ls(dag=True,type='mesh',long=True,allPaths=True,visible=True) or []))
    masks = None
    if view is not None and view.viewIsFiltered():
        # Use this panel's filter/isolation list, not the global selection.
        masks = {}
        selection = view.filteredObjectList()
        for i in range(selection.length()):
            try:
                dag, component = selection.getComponent(i)
            except RuntimeError:
                continue
            path = dag.fullPathName()
            faces = None
            if not component.isNull():
                if component.apiType() != om.MFn.kMeshPolygonComponent:
                    return []  # Do not guess unsupported component filters.
                faces = list(om.MFnSingleIndexedComponent(component).getElements())
            for candidate in paths:
                if candidate == path or candidate.startswith(path+'|'):
                    masks[candidate] = faces
    meshes = []
    for path in paths:
        if masks is not None and path not in masks:
            continue
        selection = om.MSelectionList()
        selection.add(path)
        dag = selection.getDagPath(0)
        fn = om.MFnMesh(dag)
        if fn.isIntermediateObject or not dag.isVisible():
            continue
        box = fn.boundingBox
        box.transformUsing(dag.inclusiveMatrix())
        meshes.append((path,dag,fn,box,None if masks is None else masks[path]))
    return meshes


def nearest_surface(origin, direction, meshes, max_distance=1e12):
    """All visible meshes participate, including clean/uninspected occluders.

    Equal-depth objects are ambiguous and intentionally yield no tooltip.
    Never skip the closest object just because it has no matching finding.
    """
    import maya.api.OpenMaya as om
    direction = direction.normal()
    hits = []
    for path,dag,fn,box,faces in meshes:
        # Slab broad phase avoids building native accelerators off the ray.
        low,high = 0.,max_distance
        for axis in range(3):
            d = direction[axis]
            if abs(d) < 1e-15:
                if not box.min[axis]-1e-6 <= origin[axis] <= box.max[axis]+1e-6:
                    high = -1.
                    break
            else:
                a,b = ((box.min[axis]-origin[axis])/d,(box.max[axis]-origin[axis])/d)
                low,high = max(low,min(a,b)),min(high,max(a,b))
        if high < low:
            continue
        options = {'accelParams':fn.autoUniformGridParams()}
        if faces is not None:
            if not faces:
                continue
            options.update(faceIds=sorted(faces),idsSorted=True)
        hit = fn.closestIntersection(om.MFloatPoint(origin),om.MFloatVector(direction),
                                     om.MSpace.kWorld,max_distance,False,**options)
        if hit is not None:
            hits.append((hit[1],path,dag,fn,hit))
    if not hits:
        return None
    hits.sort(key=lambda item:item[0])
    first = hits[0]
    tolerance = max(1e-5,abs(first[0])*2e-6)
    if len(hits)>1 and hits[1][0]-first[0] <= tolerance:
        return None
    return first


def segment_pixels(p,a,b):
    dx,dy = b[0]-a[0],b[1]-a[1]
    size = dx*dx+dy*dy
    t = max(0.,min(1.,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/size)) if size else 0.
    return ((p[0]-a[0]-t*dx)**2+(p[1]-a[1]-t*dy)**2)**.5


def pick(index, origin, direction, project, cursor, meshes, radius=7., max_distance=1e12):
    import maya.api.OpenMaya as om
    surface = nearest_surface(origin,direction,meshes,max_distance)
    if surface is None:
        return None
    _,path,dag,fn,hit = surface
    face,triangle = hit[2],hit[3]
    tri = fn.getPolygonTriangleVertices(face,triangle)
    points = [tuple(fn.getPoint(v,om.MSpace.kWorld))[:3] for v in tri]
    if dot(normal(points),tuple(-direction)[:3]) <= 0:
        return None  # Back-facing nearest surface blocks everything behind it.
    polygon = om.MItMeshPolygon(dag)
    polygon.setIndex(face)
    candidates = []
    for vertex in polygon.getVertices():
        if (path,'vtx',vertex) in index.issues:
            x,y,visible = project(fn.getPoint(vertex,om.MSpace.kWorld))
            if visible and ((x-cursor[0])**2+(y-cursor[1])**2)**.5 <= radius:
                candidates.append(('vtx',vertex))
    for edge in polygon.getEdges():
        if (path,'e',edge) in index.issues:
            a,b = [project(fn.getPoint(v,om.MSpace.kWorld)) for v in fn.getEdgeVertices(edge)]
            if a[2] and b[2] and segment_pixels(cursor,a,b)<=radius:
                candidates.append(('e',edge))
    candidates.append(('f',face))
    issues = [(kind,component,finding) for kind,component in candidates
              for finding in index.issues.get((path,kind,component),())]
    if not issues:
        return None
    return {'path':path,'face':face,'issues':issues}


def tooltip_html(hit):
    colors = {'error':'#ff7989','warning':'#efc16f','review':'#99bcff'}
    severity = {'error':'エラー','warning':'警告','review':'要確認'}
    rows = []
    for kind,component,finding in hit['issues'][:6]:
        rows.append('<span style="color:{}"><b>{} · {}</b></span><br>'
                    '<b>{}[{}]</b>　{}<br>'.format(colors[finding.severity],severity[finding.severity],
                    html.escape(RULE_MAP[finding.rule].title),kind,component,html.escape(finding.message)))
    if len(hit['issues'])>6:
        rows.append('ほか{}件：検査結果で確認できます。'.format(len(hit['issues'])-6))
    return '<b>問題箇所の詳細</b><br><span style="color:#8fa9c2">{}</span><hr>{}'.format(
        html.escape(hit['path']),'<br>'.join(rows))


def pick_view(index, view, x, y, radius=7.):
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaUI as omui
    if not view.objectDisplay() & omui.M3dView.kDisplayMeshes or view.displayStyle()==omui.M3dView.kBoundingBox:
        return None
    near,far = om.MPoint(),om.MPoint()
    view.viewToWorld(int(x),int(y),near,far)
    direction = far-near
    return pick(index,near,direction,view.worldToView,(x,y),scene_meshes(view),radius,direction.length())
