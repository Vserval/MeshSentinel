"""Host-independent face-corner UV connectivity validation.

Coordinates are deliberately ignored: coincident UV positions are not shared
UV IDs. No mesh data is modified by this module.
"""
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class UVTopology:
    nonmanifold_uvs: tuple
    affected_faces: tuple
    missing_faces: tuple


def analyze_uv_topology(faces, face_uvs):
    """Find disconnected UV fans, repeated corners and invalid UV edge use.

    ``faces`` and ``face_uvs`` contain one ordered vertex/UV-ID loop per face.
    An empty UV loop means unmapped. Holed polygons require separate boundary
    loops and must not be passed as a single flattened loop by the caller.
    Runtime is linear in the number of face corners.
    """
    if len(faces) != len(face_uvs):
        raise ValueError('Face and UV loop counts differ')
    corners = defaultdict(list)
    edges = defaultdict(list)
    adjacency = defaultdict(set)
    bad = set()
    missing = []
    for face, (vertices, uvs) in enumerate(zip(faces, face_uvs)):
        if len(uvs) != len(vertices) or not uvs or any(u < 0 for u in uvs):
            missing.append(face)
            continue
        seen = set()
        for i, (vertex, uv) in enumerate(zip(vertices, uvs)):
            node = (face, i)
            corners[uv].append((node, vertex))
            if uv in seen:
                bad.add(uv)
            seen.add(uv)
            j = (i + 1) % len(vertices)
            other = uvs[j]
            edges[tuple(sorted((uv, other)))].append(
                (uv, other, vertex, vertices[j], node, (face, j)))
    for (u, v), uses in edges.items():
        if u == v or len(uses) > 2:
            bad.update((u, v))
            continue
        if len(uses) == 2:
            a, b = uses
            # Continuous UV edge must correspond to the same geometric edge,
            # traversed in opposite directions by two different faces.
            if (a[0], a[1], a[2], a[3]) != (b[1], b[0], b[3], b[2]) or a[4][0] == b[4][0]:
                bad.update((u, v))
                continue
            for x, y in ((a[4], b[5]), (a[5], b[4])):
                adjacency[x].add(y)
                adjacency[y].add(x)
    for uv, uses in corners.items():
        if len({vertex for _, vertex in uses}) != 1:
            bad.add(uv)
            continue
        remaining = {node for node, _ in uses}
        stack = [remaining.pop()]
        while stack:
            for other in adjacency[stack.pop()]:
                if other in remaining:
                    remaining.remove(other)
                    stack.append(other)
        if remaining:
            bad.add(uv)
    affected = {node[0] for uv in bad for node, _ in corners[uv]}
    return UVTopology(tuple(sorted(bad)), tuple(sorted(affected)), tuple(missing))
