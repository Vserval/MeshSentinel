import csv
import datetime
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace

from mesh_sentinel.model import MeshData, Settings, Report, Finding, MeshResult
from mesh_sentinel.engine import Inspector, Cancelled
from mesh_sentinel.geometry import triangles_intersect
from mesh_sentinel.exporter import export_report


def mesh(points, faces, **kwargs):
    edges = list(dict.fromkeys(tuple(sorted((a, b))) for f in faces for a, b in zip(f, f[1:] + f[:1])))
    triangles = [(i, f[0], f[j], f[j+1]) for i, f in enumerate(faces) for j in range(1, len(f)-1)]
    return MeshData("|test|testShape", "test-id", tuple(map(tuple, points)), tuple(map(tuple, faces)),
                    tuple(edges), tuple(triangles), **kwargs)


def cube(scale=1., offset=(0., 0., 0.), reverse=False):
    points = [(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]
    points = [tuple(p[i]*scale+offset[i] for i in range(3)) for p in points]
    faces = [(0,3,2,1),(4,5,6,7),(0,1,5,4),(3,7,6,2),(0,4,7,3),(1,2,6,5)]
    if reverse:
        faces = [f[::-1] for f in faces]
    return mesh(points, faces)


def combine(a, b):
    offset = len(a.points)
    return mesh(a.points+b.points, a.faces+tuple(tuple(v+offset for v in f) for f in b.faces))


class EngineTests(unittest.TestCase):
    def inspect(self, m, keys=None, **kwargs):
        settings = Settings(**kwargs) if keys is None else Settings(enabled=tuple(keys), **kwargs)
        self.result = Inspector(settings).inspect(m)
        self.assertFalse(self.result.notes, self.result.notes)
        return self.result

    def ids(self, key, component="f"):
        return set(v for f in self.result.findings if f.rule == key and f.component == component for v in f.ids)

    def test_clean_cube_all_checks(self):
        r = self.inspect(cube())
        self.assertEqual(r.findings, [])
        self.assertTrue(all(s == "complete" for s in r.checks.values()))

    def test_open_quad(self):
        self.inspect(mesh([(0,0,0),(1,0,0),(1,1,0),(0,1,0)], [(0,1,2,3)]))
        self.assertEqual(self.ids("boundary", "e"), {0,1,2,3})
        self.assertFalse(self.ids("normals"))

    def test_nonmanifold_edge(self):
        m = mesh([(0,0,0),(1,0,0),(0,1,0),(0,-1,0),(0,0,1)], [(0,1,2),(1,0,3),(0,1,4)])
        self.inspect(m, ["nonmanifold"])
        self.assertEqual(self.ids("nonmanifold", "e"), {0})
        self.assertEqual(self.ids("nonmanifold", "vtx"), {0,1})

    def test_bow_tie_vertex(self):
        self.inspect(mesh([(0,0,0),(1,0,0),(0,1,0),(-1,0,0),(0,-1,0)], [(0,1,2),(0,3,4)]), ["nonmanifold"])
        self.assertEqual(self.ids("nonmanifold", "vtx"), {0})

    def test_degenerate_collinear(self):
        self.inspect(mesh([(0,0,0),(1,0,0),(2,0,0)], [(0,1,2)]), ["degenerate"])
        self.assertEqual(self.ids("degenerate"), {0})

    def test_zero_edge(self):
        self.inspect(mesh([(0,0,0),(0,0,0),(1,1,0)], [(0,1,2)]), ["zero_edge"])
        self.assertEqual(self.ids("zero_edge", "e"), {0})

    def test_duplicate_vertex_neighbor_cells(self):
        self.inspect(mesh([(-.0000001,0,0),(.0000001,0,0),(2,0,0)], []), ["duplicate_vertex"])
        self.assertEqual(self.ids("duplicate_vertex", "vtx"), {0,1})

    def test_duplicate_vertex_outside_tolerance(self):
        self.inspect(mesh([(0,0,0),(.00002,0,0)], []), ["duplicate_vertex"])
        self.assertFalse(self.ids("duplicate_vertex", "vtx"))

    def test_duplicate_face_separate_ids_reversed_cycle(self):
        p = [(0,0,0),(1,0,0),(0,1,0)]
        self.inspect(mesh(p+p, [(0,1,2),(4,3,5)]), ["duplicate_face"])
        self.assertEqual(self.ids("duplicate_face"), {0,1})

    def test_duplicate_face_per_vertex_tolerance(self):
        p = [(0,0,0),(1,0,0),(0,1,0)]
        q = [(x+.000009,y,z) for x,y,z in p]
        self.inspect(mesh(p+q, [(0,1,2),(3,4,5)]), ["duplicate_face"])
        self.assertEqual(self.ids("duplicate_face"), {0,1})

    def test_duplicate_face_not_same_unordered_loop(self):
        self.inspect(mesh([(0,0,0),(1,0,0),(1,1,0),(0,1,0)], [(0,1,2,3),(0,2,1,3)]), ["duplicate_face"])
        self.assertFalse(self.ids("duplicate_face"))

    def test_internal_nested_cube(self):
        self.inspect(combine(cube(3), cube(.5)), ["internal"])
        self.assertEqual(self.ids("internal"), set(range(6,12)))

    def test_internal_isolated_face(self):
        self.inspect(combine(cube(3), mesh([(0,0,0),(1,0,0),(0,1,0)], [(0,1,2)])), ["internal"])
        self.assertEqual(self.ids("internal"), {6})

    def test_internal_exterior_and_surface_faces(self):
        face = mesh([(3,0,0),(3,1,0),(3,0,1)], [(0,1,2)])
        self.inspect(combine(cube(3), face), ["internal"])
        self.assertFalse(self.ids("internal"))

    def test_internal_crossing_is_not_inside(self):
        face = mesh([(0,0,0),(5,0,0),(0,1,0)], [(0,1,2)])
        self.inspect(combine(cube(3), face), ["internal"])
        self.assertFalse(self.ids("internal"))

    def test_open_container_not_used(self):
        c = cube(3)
        c = mesh(c.points, c.faces[:-1])
        self.inspect(combine(c, cube(.5)), ["internal"])
        self.assertFalse(self.ids("internal"))

    def test_crossing_triangles(self):
        p = [(-1,-1,0),(1,-1,0),(0,1,0),(0,0,-1),(0,0,1),(.5,.5,0)]
        self.inspect(mesh(p, [(0,1,2),(3,4,5)]), ["intersection"])
        self.assertEqual(self.ids("intersection"), {0,1})

    def test_coplanar_overlap(self):
        self.inspect(mesh([(0,0,0),(2,0,0),(0,2,0),(.1,.1,0),(.8,.1,0),(.1,.8,0)], [(0,1,2),(3,4,5)]), ["intersection"])
        self.assertEqual(self.ids("intersection"), {0,1})

    def test_adjacent_triangles_are_clean(self):
        self.inspect(mesh([(0,0,0),(1,0,0),(1,1,0),(0,1,0)], [(0,1,2),(0,2,3)]), ["intersection"])
        self.assertFalse(self.ids("intersection"))

    def test_shared_vertex_does_not_hide_crossing(self):
        p = [(0,0,0),(2,0,0),(0,2,0),(1,1,-1),(1,1,1)]
        self.inspect(mesh(p, [(0,1,2),(0,3,4)]), ["intersection"])
        self.assertEqual(self.ids("intersection"), {0,1})

    def test_shared_edge_fold_overlap(self):
        self.inspect(mesh([(0,0,0),(2,0,0),(0,2,0),(1,1,0)], [(0,1,2),(1,0,3)]), ["intersection"])
        self.assertEqual(self.ids("intersection"), {0,1})

    def test_reversed_closed_shell(self):
        self.inspect(cube(reverse=True), ["normals"])
        self.assertEqual(self.ids("normals"), set(range(6)))

    def test_translated_inward_volume(self):
        self.inspect(cube(offset=(1e9,1e9,1e9), reverse=True), ["normals"])
        self.assertEqual(self.ids("normals"), set(range(6)))

    def test_winding_conflict(self):
        self.inspect(mesh([(0,0,0),(1,0,0),(0,1,0),(0,-1,0)], [(0,1,2),(0,1,3)]), ["normals"])
        self.assertEqual(self.ids("normals"), {0,1})

    def test_t_junction(self):
        p = [(0,0,0),(2,0,0),(0,2,0),(1,0,0),(1,-1,0),(2,-1,0)]
        self.inspect(mesh(p, [(0,1,2),(3,4,5)]), ["t_junction"])
        self.assertEqual(self.ids("t_junction", "vtx"), {3})
        self.assertEqual(self.ids("t_junction", "e"), {0})

    def test_t_junction_excludes_endpoints(self):
        p = [(0,0,0),(2,0,0),(0,2,0),(0,0,0)]
        self.inspect(mesh(p, [(0,1,2)]), ["t_junction"])
        self.assertFalse(self.ids("t_junction", "vtx"))

    def test_sliver(self):
        self.inspect(mesh([(0,0,0),(10,0,0),(0,.001,0)], [(0,1,2)]), ["sliver"])
        self.assertEqual(self.ids("sliver"), {0})

    def test_ngon_and_concave(self):
        self.inspect(mesh([(0,0,0),(2,0,0),(1,.5,0),(2,2,0),(0,2,0)], [(0,1,2,3,4)], concave_faces=(0,)), ["ngon","concave"])
        self.assertEqual(self.ids("ngon"), {0})
        self.assertEqual(self.ids("concave"), {0})

    def test_holed_face(self):
        self.inspect(replace(cube(), holed_faces=(2,)), ["boundary"])
        self.assertEqual(self.ids("boundary"), {2})

    def test_disabled_checks(self):
        r = self.inspect(cube(reverse=True), ["ngon"])
        self.assertEqual(r.checks["normals"], "disabled")
        self.assertEqual(r.findings, [])

    def test_budget_is_not_reported_as_clean(self):
        r = Inspector(Settings(pair_budget=1)).inspect(mesh([(0,0,0)]*10, []))
        self.assertEqual(r.checks["duplicate_vertex"], "incomplete")
        report = Report("now", Settings(), [r])
        self.assertFalse(report.complete)
        self.assertTrue(r.notes)

    def test_cancellation(self):
        with self.assertRaises(Cancelled):
            Inspector(Settings(), cancelled=lambda: True).inspect(cube())

    def test_cancellation_during_check_marks_remaining(self):
        state = {"cancel": False}
        def progress(i, total, title):
            if i == 3:
                state["cancel"] = True
        r = Inspector(Settings(), lambda: state["cancel"], progress).inspect(cube())
        self.assertEqual(r.checks["duplicate_vertex"], "cancelled")
        self.assertEqual(r.checks["concave"], "not_run")

    def test_bad_settings_rejected(self):
        for args in ({"distance":0}, {"area":float("nan")}, {"aspect":-1}, {"enabled":("bad",)}):
            with self.assertRaises(ValueError):
                Settings(**args)

    def test_signature_detects_moved_vertices(self):
        c = cube()
        self.assertNotEqual(c.signature(), replace(c, points=((99,0,0),)+c.points[1:]).signature())

    def test_export_roundtrip_and_escaping(self):
        r = self.inspect(cube())
        r.path = "=malicious<script>"
        r.findings.append(Finding("ngon", r.path, "f", [0,1], "<script>alert(1)</script>"))
        report = Report("now", Settings(), [r], errors=["=bad"])
        with tempfile.TemporaryDirectory() as tmp:
            for ext in ("json","csv","html"):
                path = os.path.join(tmp, "report." + ext)
                export_report(report, path)
                with open(path, encoding="utf-8-sig") as f:
                    data = f.read()
                if ext == "json":
                    self.assertFalse(json.loads(data)["complete"])
                elif ext == "html":
                    self.assertNotIn("<script>", data)
                    self.assertIn("&lt;script&gt;", data)
                else:
                    rows = list(csv.reader(io.StringIO(data)))
                    self.assertTrue(any(row[1].startswith("'=malicious") for row in rows))
                    self.assertTrue(any(row[0] == "check" for row in rows))


if __name__ == "__main__":
    unittest.main()
