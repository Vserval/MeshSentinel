"""Mesh checks on immutable snapshots. Safe to run outside Maya's main thread."""
from collections import defaultdict
import itertools
import math
import time

from .model import MeshResult, Finding, RULES
from .geometry import (sub, dot, cross, length, normal, bounds, BVH, segment_distance,
                       triangles_intersect, inside_shell)


class Cancelled(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class Inspector:
    def __init__(self, settings, cancelled=lambda: False, progress=lambda *args: None):
        self.settings = settings
        self.cancelled = cancelled
        self.progress = progress
        self.ops = 0

    def checkpoint(self):
        if self.cancelled():
            raise Cancelled()

    def tick(self):
        self.checkpoint()
        self.ops += 1
        if self.ops > self.settings.pair_budget:
            raise BudgetExceeded()

    def inspect(self, mesh):
        self.mesh = mesh
        self.result = MeshResult(mesh.path, mesh.uuid, mesh.fingerprint or mesh.signature(),
                                 len(mesh.points), len(mesh.faces))
        start = time.perf_counter()
        self.edge_faces = defaultdict(list)
        self.vertex_faces = defaultdict(set)
        self.face_edges = []
        self.face_triangles = defaultdict(list)
        self.tris = []
        for f, vs in enumerate(mesh.faces):
            self.checkpoint()
            keys = []
            for a, b in zip(vs, vs[1:] + vs[:1]):
                key = tuple(sorted((a, b)))
                self.edge_faces[key].append((f, a, b))
                self.vertex_faces[a].add(f)
                keys.append(key)
            self.face_edges.append(keys)
        if mesh.edge_incidence:
            self.edge_faces = defaultdict(list)
            self.face_edges = [[] for _ in mesh.faces]
            for edge, entries in zip(mesh.edges, mesh.edge_incidence):
                self.checkpoint()
                key = tuple(sorted(edge))
                self.edge_faces[key].extend(entries)
                for f, _, _ in entries:
                    self.face_edges[f].append(key)
        for i, (f, a, b, c) in enumerate(mesh.triangles):
            self.checkpoint()
            self.face_triangles[f].append(i)
            self.tris.append((mesh.points[a], mesh.points[b], mesh.points[c]))
        self._shells = None
        self._triangle_tree = None
        for index, rule in enumerate(RULES):
            if rule.key not in self.settings.enabled:
                self.result.checks[rule.key] = "disabled"
                continue
            self.progress(index, len(RULES), rule.title)
            self.ops = 0
            try:
                self.checkpoint()
                getattr(self, "check_" + rule.key)()
                self.result.checks[rule.key] = "complete"
            except Cancelled:
                self.result.checks[rule.key] = "cancelled"
                for rest in RULES[index+1:]:
                    self.result.checks[rest.key] = "not_run" if rest.key in self.settings.enabled else "disabled"
                break
            except BudgetExceeded:
                self.result.checks[rule.key] = "incomplete"
                self.result.notes.append(rule.title + ": 比較上限に到達。閾値設定で上限を増やして再検査してください。")
            except Exception as exc:
                self.result.checks[rule.key] = "failed"
                self.result.notes.append("{}: {}: {}".format(rule.title, type(exc).__name__, exc))
        self.result.seconds = time.perf_counter() - start
        return self.result

    def emit(self, key, component, ids, message):
        if ids:
            self.result.findings.append(Finding(key, self.mesh.path, component, sorted(set(ids)), message))

    def check_nonmanifold(self):
        bad_edges, bad_vertices = set(), set()
        vertex_edges = defaultdict(list)
        for eid, (a, b) in enumerate(self.mesh.edges):
            self.checkpoint()
            entries = self.edge_faces[tuple(sorted((a, b)))]
            if len(entries) > 2:
                bad_edges.add(eid)
                bad_vertices.update((a, b))
            vertex_edges[a].append(entries)
            vertex_edges[b].append(entries)
        for v, faces in self.vertex_faces.items():
            self.checkpoint()
            graph = {f: set() for f in faces}
            for entries in vertex_edges[v]:
                fs = [f for f, _, _ in entries]
                if fs:
                    for f in fs[1:]:
                        graph[fs[0]].add(f)
                        graph[f].add(fs[0])
            seen, stack = set(), [next(iter(faces))]
            while stack:
                f = stack.pop()
                if f not in seen:
                    seen.add(f)
                    stack.extend(graph[f] - seen)
            if seen != faces:
                bad_vertices.add(v)
        self.emit("nonmanifold", "e", bad_edges, "3面以上が同一エッジを共有")
        self.emit("nonmanifold", "vtx", bad_vertices, "非多様体エッジの端点、または分離した面ファンを持つ頂点")

    def check_degenerate(self):
        bad = set(self.mesh.invalid_faces)
        for f, vs in enumerate(self.mesh.faces):
            self.checkpoint()
            area = sum(length(normal(self.tris[i])) * .5 for i in self.face_triangles[f])
            if len(vs) < 3 or len(set(vs)) != len(vs) or area <= self.settings.area:
                bad.add(f)
        self.emit("degenerate", "f", bad, "面積閾値以下、頂点の繰り返し、または無効な三角形分割")

    def check_zero_edge(self):
        bad = []
        for i, (a, b) in enumerate(self.mesh.edges):
            self.checkpoint()
            if length(sub(self.mesh.points[a], self.mesh.points[b])) <= self.settings.distance:
                bad.append(i)
        self.emit("zero_edge", "e", bad, "長さ ≤ {:g} cm".format(self.settings.distance))

    def check_duplicate_vertex(self):
        grid, bad = defaultdict(list), set()
        eps = self.settings.distance
        try:
            for i, p in enumerate(self.mesh.points):
                self.checkpoint()
                cell = tuple(math.floor(x/eps) for x in p)
                for offset in itertools.product((-1, 0, 1), repeat=3):
                    for j in grid.get(tuple(a+b for a, b in zip(cell, offset)), ()):
                        self.tick()
                        if length(sub(p, self.mesh.points[j])) <= eps:
                            bad.update((i, j))
                grid[cell].append(i)
        finally:
            self.emit("duplicate_vertex", "vtx", bad, "同一メッシュ内の別頂点と距離許容誤差以内")

    def check_duplicate_face(self):
        grid, bad = defaultdict(list), set()
        eps = self.settings.distance
        centers = []
        try:
            for f, vs in enumerate(self.mesh.faces):
                self.checkpoint()
                if not vs:
                    centers.append((0., 0., 0.))
                    continue
                pts = [self.mesh.points[v] for v in vs]
                center = tuple(sum(p[i] for p in pts)/len(pts) for i in range(3))
                centers.append(center)
                cell = tuple(math.floor(x/eps) for x in center)
                for offset in itertools.product((-1, 0, 1), repeat=3):
                    key = (len(vs),) + tuple(a+b for a, b in zip(cell, offset))
                    for g in grid.get(key, ()):
                        self.tick()
                        other = self.mesh.faces[g]
                        matched = False
                        for start in range(len(vs)):
                            for direction in (1, -1):
                                good = True
                                for k, p in enumerate(pts):
                                    self.tick()
                                    if length(sub(p, self.mesh.points[other[(start+direction*k)%len(vs)]])) > eps:
                                        good = False
                                        break
                                if good:
                                    correspondence = {v: other[(start+direction*k)%len(vs)] for k,v in enumerate(vs)}
                                    mapped_edges = {tuple(sorted((correspondence[a],correspondence[b]))) for a,b in self.face_edges[f]}
                                    if mapped_edges == set(self.face_edges[g]):
                                        matched = True
                                        break
                            if matched:
                                break
                        if matched:
                            bad.update((f, g))
                grid[(len(vs),) + cell].append(f)
        finally:
            self.emit("duplicate_face", "f", bad, "巡回頂点列が幾何的に一致（逆向きを含む）")

    def shells(self):
        if self._shells is not None:
            return self._shells
        graph = defaultdict(set)
        for entries in self.edge_faces.values():
            self.checkpoint()
            fs = [f for f, _, _ in entries]
            for f in fs[1:]:
                graph[fs[0]].add(f)
                graph[f].add(fs[0])
        unseen, shells = set(range(len(self.mesh.faces))), []
        while unseen:
            stack, group = [unseen.pop()], set()
            while stack:
                self.checkpoint()
                f = stack.pop()
                group.add(f)
                for g in graph[f] & unseen:
                    unseen.remove(g)
                    stack.append(g)
            closed = all(len(self.edge_faces[e]) == 2 for f in group for e in self.face_edges[f])
            # A closed edge incidence alone does not guarantee valid volume.
            valid = closed and not (group & set(self.mesh.invalid_faces)) and all(
                self.face_triangles[f] and sum(length(normal(self.tris[i])) for i in self.face_triangles[f]) > self.settings.area*2
                for f in group)
            coherent = valid and all(entries[0][1:] == entries[1][1:][::-1]
                                     for f in group for e in self.face_edges[f] for entries in [self.edge_faces[e]])
            shells.append((group, bool(coherent)))
        self._shells = shells
        return shells

    def triangle_tree(self):
        if self._triangle_tree is None:
            self._triangle_tree = BVH([bounds(t) for t in self.tris], self.checkpoint)
        return self._triangle_tree

    def check_internal(self):
        bad = set()
        eps = self.settings.distance
        try:
            for group, closed in self.shells():
                self.checkpoint()
                if not closed or len(group) == len(self.mesh.faces):
                    continue
                tids = [i for f in group for i in self.face_triangles[f]]
                triangles = [self.tris[i] for i in tids]
                tree = BVH([bounds(t) for t in triangles], self.checkpoint)
                # Exclude intersecting/overlapping containers; volume parity is undefined there.
                valid_container = True
                for i, t in enumerate(triangles):
                    for j in tree.query(bounds(t), eps):
                        self.tick()
                        if j <= i or self.mesh.triangles[tids[i]][0] == self.mesh.triangles[tids[j]][0]:
                            continue
                        common = set(self.mesh.triangles[tids[i]][1:]) & set(self.mesh.triangles[tids[j]][1:])
                        if triangles_intersect(t, triangles[j], [self.mesh.points[v] for v in common], eps, self.settings.area):
                            valid_container = False
                            break
                    if not valid_container:
                        break
                if not valid_container:
                    self.result.notes.append("内部面: 自己交差を持つ閉殻を包含判定の容器から除外しました。")
                    continue
                for f, vs in enumerate(self.mesh.faces):
                    self.checkpoint()
                    if f in group or f in bad or not self.face_triangles[f]:
                        continue
                    samples = [self.mesh.points[v] for v in set(vs)]
                    samples += [tuple(sum(p[k] for p in self.tris[i])/3 for k in range(3)) for i in self.face_triangles[f]]
                    if not all(inside_shell(p, triangles, tree, eps, self.tick) for p in samples):
                        continue
                    crossing = False
                    for i in self.face_triangles[f]:
                        for j in tree.query(bounds(self.tris[i]), eps):
                            self.tick()
                            if triangles_intersect(self.tris[i], triangles[j], [], eps, self.settings.area):
                                crossing = True
                                break
                        if crossing:
                            break
                    if not crossing:
                        bad.add(f)
        finally:
            self.emit("internal", "f", bad, "有効な閉殻内の面。意図した内殻・空洞か確認してください")

    def check_intersection(self):
        tree, bad = self.triangle_tree(), set()
        try:
            for i, t in enumerate(self.tris):
                self.checkpoint()
                fi, *vi = self.mesh.triangles[i]
                for j in tree.query(bounds(t), self.settings.distance):
                    self.tick()
                    if j <= i:
                        continue
                    fj, *vj = self.mesh.triangles[j]
                    if fi == fj:
                        continue
                    shared = [self.mesh.points[v] for v in set(vi) & set(vj)]
                    if triangles_intersect(t, self.tris[j], shared, self.settings.distance, self.settings.area):
                        bad.update((fi, fj))
        finally:
            self.emit("intersection", "f", bad, "同一メッシュ内の交差・共面重複。接続していない面同士の非共面接触を含む")

    def check_normals(self):
        winding = set()
        for entries in self.edge_faces.values():
            self.checkpoint()
            if len(entries) == 2 and entries[0][1:] == entries[1][1:]:
                winding.update(e[0] for e in entries)
        self.emit("normals", "f", winding, "共有辺の向きが同じ隣接面。どちらを反転すべきかは要確認")
        inward = set()
        for fs, closed in self.shells():
            self.checkpoint()
            if not closed:
                continue
            origin = self.mesh.points[self.mesh.faces[next(iter(fs))][0]]
            volume = 0.
            for f in fs:
                for i in self.face_triangles[f]:
                    a, b, c = (sub(p, origin) for p in self.tris[i])
                    volume += dot(a, cross(b, c))/6
            if volume < -(self.settings.distance ** 3):
                inward.update(fs)
        self.emit("normals", "f", inward, "閉殻の符号付き体積が負。空洞の内壁・ミラー変換の場合は意図を確認")
        self.emit("normals", "f", self.mesh.opposing_normals, "幾何法線と逆向きのフェース頂点法線を含む")

    def check_boundary(self):
        bad = []
        for i, e in enumerate(self.mesh.edges):
            self.checkpoint()
            if len(self.edge_faces[tuple(sorted(e))]) == 1:
                bad.append(i)
        self.emit("boundary", "e", bad, "開いた境界。意図した開口も対象")
        self.emit("boundary", "f", self.mesh.holed_faces, "Maya APIが穴付きポリゴンと判定")

    def check_t_junction(self):
        tree = BVH([(p, p) for p in self.mesh.points], self.checkpoint)
        bad_v, bad_e = set(), set()
        eps = self.settings.distance
        try:
            for eid, (a, b) in enumerate(self.mesh.edges):
                self.checkpoint()
                pa, pb = self.mesh.points[a], self.mesh.points[b]
                edge_length = length(sub(pb, pa))
                if edge_length <= 2*eps:
                    continue
                attached = {f for f, _, _ in self.edge_faces[tuple(sorted((a, b)))]}
                for v in tree.query(bounds((pa, pb)), eps):
                    self.tick()
                    if v in (a, b) or self.vertex_faces[v] & attached:
                        continue
                    distance, t = segment_distance(self.mesh.points[v], pa, pb)
                    if distance <= eps and eps/edge_length < t < 1-eps/edge_length:
                        bad_v.add(v)
                        bad_e.add(eid)
        finally:
            self.emit("t_junction", "vtx", bad_v, "非接続エッジの内部に位置する頂点")
            self.emit("t_junction", "e", bad_e, "内部に非接続頂点があるエッジ")

    def check_sliver(self):
        bad = []
        for f, vs in enumerate(self.mesh.faces):
            self.checkpoint()
            if len(vs) == 3:
                t = [self.mesh.points[v] for v in vs]
                double_area = length(normal(t))
                longest = max(dot(sub(t[i], t[(i+1)%3]), sub(t[i], t[(i+1)%3])) for i in range(3))
                if double_area > 2*self.settings.area and longest/double_area >= self.settings.aspect:
                    bad.append(f)
        self.emit("sliver", "f", bad, "最長辺 / 最小高度 ≥ {:g}（三角形面のみ）".format(self.settings.aspect))

    def check_ngon(self):
        bad = []
        for f, vs in enumerate(self.mesh.faces):
            self.checkpoint()
            if len(vs) >= 5:
                bad.append(f)
        self.emit("ngon", "f", bad, "5頂点以上のポリゴン")

    def check_concave(self):
        self.emit("concave", "f", [f for f in self.mesh.concave_faces if len(self.mesh.faces[f]) >= 5],
                  "凹形状のN-gon。非平面ポリゴンでは三角形分割も確認してください")
