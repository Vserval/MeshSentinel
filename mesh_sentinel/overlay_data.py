"""Build bounded, host-independent batches for viewport diagnostic drawing."""
from collections import defaultdict
from .geometry import normal, add, length, mul, dot, sub

COLORS = {"error": (1., .22, .29), "warning": (1., .69, .20), "review": (.35, .63, 1.)}
PRIORITY = {"error": 3, "warning": 2, "review": 1}


def build_batches(findings, meshes, limit=50000):
    """Use native component IDs; a component is never partially displayed."""
    unique = {}
    for finding in findings:
        for index in finding.ids:
            key = (finding.mesh, finding.component, index)
            previous = unique.get(key)
            if previous is None or PRIORITY[finding.severity] > PRIORITY[previous]:
                unique[key] = finding.severity
    mesh_map = {m.path: m for m in meshes}
    face_triangles, face_edges, face_planes, vertex_faces, edge_faces = {}, {}, {}, {}, {}
    for m in meshes:
        tri_map, edge_map = defaultdict(list), defaultdict(list)
        for f, a, b, c in m.triangles:
            tri_map[f].append((a, b, c))
        if m.edge_incidence:
            for edge, incidence in zip(m.edges, m.edge_incidence):
                for f, _, _ in incidence:
                    edge_map[f].append(edge)
        else:
            for f, vs in enumerate(m.faces):
                edge_map[f] = list(zip(vs, vs[1:] + vs[:1]))
        face_triangles[m.path], face_edges[m.path] = tri_map, edge_map
        planes, vf, ef = {}, defaultdict(list), defaultdict(list)
        for f, vs in enumerate(m.faces):
            n = (0., 0., 0.)
            for tri in tri_map[f]:
                n = add(n, normal([m.points[v] for v in tri]))
            size = length(n)
            center = tuple(sum(m.points[v][k] for v in vs)/len(vs) for k in range(3)) if vs else (0.,0.,0.)
            planes[f] = (center, mul(n, 1/size) if size else n)
            for v in vs:
                vf[v].append(f)
            for edge in edge_map[f]:
                ef[tuple(sorted(edge))].append(f)
        face_planes[m.path], vertex_faces[m.path], edge_faces[m.path] = planes, vf, ef
    batches = {s: {"severity": s, "points": [], "lines": [], "triangles": [], "labels": [], "records": []} for s in COLORS}
    used = displayed = 0
    components = []
    for (path, kind, index), severity in sorted(unique.items(), key=lambda kv: (-PRIORITY[kv[1]], kv[0])):
        m = mesh_map[path]
        points, lines, triangles = [], [], []
        if kind == "vtx":
            points = [m.points[index]]
            center = points[0]
            adjacent = vertex_faces[path][index]
        elif kind == "e":
            lines = [m.points[v] for v in m.edges[index]]
            center = tuple(sum(p[k] for p in lines)/2 for k in range(3))
            if lines[0] == lines[1]:
                points = [center]
            adjacent = edge_faces[path][tuple(sorted(m.edges[index]))]
        else:
            vs = m.faces[index]
            if not vs:
                continue
            triangles = [m.points[v] for tri in face_triangles[path][index] for v in tri]
            lines = [m.points[v] for edge in face_edges[path][index] for v in edge]
            center = tuple(sum(m.points[v][k] for v in vs)/len(vs) for k in range(3))
            # Keep a marker for collapsed faces; normal faces need no center dot.
            if not triangles or face_planes[path][index][1] == (0.,0.,0.):
                points = [center]
            adjacent = [index]
        cost = len(points) + len(lines)//2 + len(triangles)//3
        if used + cost > limit:
            continue
        used += cost
        displayed += 1
        components.append((path,kind,index))
        target = batches[severity]
        ranges = {name: (len(target[name]), len(target[name])+len(values))
                  for name, values in (("points",points),("lines",lines),("triangles",triangles))}
        target["points"].extend(points)
        target["lines"].extend(lines)
        target["triangles"].extend(triangles)
        label_index = None
        if sum(len(b["labels"]) for b in batches.values()) < 60:
            label_index = len(target["labels"])
            target["labels"].append((center, "{}[{}]".format(kind, index)))
        target["records"].append((tuple(face_planes[path][f] for f in adjacent), ranges, label_index))
    return {"batches": tuple(b for b in batches.values() if b["points"] or b["lines"] or b["triangles"]),
            "displayed": displayed, "total": len(unique), "primitives": used, "truncated": displayed < len(unique),
            "components": tuple(components)}


def visible_indices(batch, eye, toward_eye=None):
    """Cull components only if all attached faces point away from this camera.

    Orthographic cameras use a constant direction. Undefined normals remain
    visible so a collapsed face/loose vertex can still be diagnosed.
    Scene occlusion is handled by VP2's depth buffer, not by per-frame ray casts.
    """
    indices = {name: [] for name in ("points", "lines", "triangles", "labels")}
    for planes, ranges, label_index in batch["records"]:
        if planes and not any(n == (0.,0.,0.) or dot(n, toward_eye if toward_eye is not None else sub(eye, p)) >= 0.
                              for p, n in planes):
            continue
        for name, (start, end) in ranges.items():
            indices[name].extend(range(start, end))
        if label_index is not None:
            indices["labels"].append(label_index)
    return indices
