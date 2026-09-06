"""Run using mayapy, in a separate process. Creates disposable test scenes."""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
import maya.api.OpenMaya as om
from mesh_sentinel.maya_bridge import mesh_paths, snapshot, select_findings
from mesh_sentinel.engine import Inspector
from mesh_sentinel.model import Settings
from create_demo import create_mesh


class MayaIntegration(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)

    def inspect(self, transform):
        cmds.select(transform)
        self.data = snapshot(mesh_paths()[0])
        self.result = Inspector(Settings()).inspect(self.data)
        self.assertFalse(self.result.notes, self.result.notes)
        return self.result

    def test_cube_native_snapshot(self):
        r = self.inspect(cmds.polyCube()[0])
        self.assertEqual((r.vertices, r.faces), (8,6))
        self.assertFalse(r.findings)

    def test_sphere_no_false_intersections(self):
        r = self.inspect(cmds.polySphere(subdivisionsX=12, subdivisionsY=8)[0])
        self.assertFalse(r.findings, r.findings)

    def test_ngon_concavity_native(self):
        obj = create_mesh("concave", [(0,0,0),(3,0,0),(3,2,0),(1.5,.8,0),(0,2,0)], [(0,1,2,3,4)])
        self.inspect(obj)
        self.assertEqual(self.data.concave_faces, (0,))
        self.assertEqual({f.rule for f in self.result.findings}, {"boundary", "ngon", "concave"})

    def test_native_nonmanifold_edge(self):
        obj = create_mesh("nm", [(0,0,0),(1,0,0),(0,1,0),(0,-1,0),(0,0,1)], [(0,1,2),(1,0,3),(0,1,4)])
        self.inspect(obj)
        found = [f for f in self.result.findings if f.rule == "nonmanifold" and f.component == "e"]
        self.assertEqual(len(found), 1)
        select_findings(found, [self.result])
        self.assertEqual(len(cmds.ls(selection=True, flatten=True)), 1)

    def test_maya_selection_component_ids(self):
        obj = cmds.polyPlane(subdivisionsX=1, subdivisionsY=1)[0]
        self.inspect(obj)
        findings = [f for f in self.result.findings if f.rule == "boundary"]
        select_findings(findings, [self.result])
        dag, component = om.MGlobal.getActiveSelectionList().getComponent(0)
        self.assertEqual(dag.fullPathName(), self.data.path)
        self.assertEqual(set(om.MFnSingleIndexedComponent(component).getElements()), set(findings[0].ids))

    def test_stale_points_block_selection(self):
        obj = cmds.polyPlane(subdivisionsX=1, subdivisionsY=1)[0]
        self.inspect(obj)
        cmds.move(1,0,0,obj+".vtx[0]", relative=True)
        with self.assertRaisesRegex(RuntimeError, "再検査"):
            select_findings(self.result.findings, [self.result])

    def test_stale_transform_block_selection(self):
        obj = cmds.polyPlane()[0]
        self.inspect(obj)
        cmds.move(1,0,0,obj)
        with self.assertRaisesRegex(RuntimeError, "再検査"):
            select_findings(self.result.findings, [self.result])

    def test_renamed_mesh_block_selection(self):
        obj = cmds.polyPlane()[0]
        self.inspect(obj)
        cmds.rename(obj, "renamed")
        with self.assertRaisesRegex(RuntimeError, "再検査"):
            select_findings(self.result.findings, [self.result])

    def test_nested_group_scope(self):
        one = cmds.polyCube()[0]
        two = cmds.polyCube()[0]
        group = cmds.group(one, cmds.group(two))
        cmds.select(group)
        self.assertEqual(len(mesh_paths()), 2)

    def test_component_selection_scope(self):
        one = cmds.polyCube()[0]
        cmds.select(one+".f[1]")
        self.assertEqual(len(mesh_paths()), 1)

    def test_instance_paths_preserved(self):
        original = cmds.polyCube()[0]
        instance = cmds.instance(original)[0]
        cmds.move(4,0,0,instance)
        paths = mesh_paths("scene")
        self.assertEqual(len(paths), 2)
        a,b = map(snapshot, paths)
        self.assertEqual(a.uuid, b.uuid)
        self.assertNotEqual(a.points, b.points)
        cmds.select(instance)
        self.assertEqual(len(mesh_paths()), 1)
        self.assertTrue(mesh_paths()[0].startswith("|"+instance+"|"))

    def test_intermediate_shapes_excluded(self):
        cmds.polyCube()
        hidden = cmds.createNode("mesh", name="intermediate")
        cmds.setAttr(hidden+".intermediateObject", True)
        self.assertEqual(len(mesh_paths("scene")), 1)

    def test_world_cm_independent_of_display_unit(self):
        obj = cmds.polyCube()[0]
        cmds.select(obj)
        path = mesh_paths()[0]
        a = snapshot(path)
        cmds.currentUnit(linear="m")
        b = snapshot(path)
        self.assertEqual(a.points, b.points)
        cmds.currentUnit(linear="cm")

    def test_native_normal_reverse(self):
        obj = cmds.polyCube()[0]
        cmds.polyNormal(obj, normalMode=0, userNormalMode=0, constructionHistory=False)
        r = self.inspect(obj)
        self.assertEqual(set(i for f in r.findings if f.rule == "normals" for i in f.ids), set(range(6)))

    def test_native_custom_normals(self):
        obj = cmds.polyPlane(subdivisionsX=1, subdivisionsY=1)[0]
        cmds.polyNormalPerVertex(obj + ".vtx[*]", xyz=(0,-1,0))
        self.inspect(obj)
        self.assertEqual(self.data.opposing_normals, (0,))

    def test_native_holed_polygon_all_boundary_edges(self):
        obj = cmds.polyCreateFacet(p=[(0,0,0),(10,0,0),(10,10,0),(0,10,0),(),
                                     (4,2,0),(5,4,0),(6,2,0),(),(5,6,0),(4,8,0),(6,8,0)])[0]
        self.inspect(obj)
        self.assertEqual(self.data.holed_faces, (0,))
        edges = {i for f in self.result.findings if f.rule == "boundary" and f.component == "e" for i in f.ids}
        self.assertEqual(edges, set(range(10)))
        self.assertFalse([f for f in self.result.findings if f.rule in ("nonmanifold", "degenerate", "normals")])

    def test_focus_resolves_panel_camera(self):
        self.inspect(cmds.polyPlane(subdivisionsX=1, subdivisionsY=1)[0])
        with patch.object(cmds, "getPanel", side_effect=lambda **kw: "modelPanel4" if "withFocus" in kw else "modelPanel"), \
             patch.object(cmds, "modelPanel", return_value="persp") as model_panel, \
             patch.object(cmds, "viewFit") as view_fit, patch.object(cmds,"setFocus"):
            select_findings(self.result.findings, [self.result], frame=True)
            model_panel.assert_called_once_with("modelPanel4", query=True, camera=True)
            view_fit.assert_called_once_with("persp", animate=False,fitFactor=.85)

    def test_inspection_does_not_mutate_scene(self):
        obj = cmds.polyCube()[0]
        cmds.file(modified=False)
        selection = cmds.ls(selection=True, long=True)
        self.inspect(obj)
        self.assertFalse(cmds.file(query=True, modified=True))
        self.assertEqual(cmds.ls(selection=True, long=True), selection)


if __name__ == "__main__":
    try:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MayaIntegration))
    finally:
        maya.standalone.uninitialize()
    sys.exit(0 if result.wasSuccessful() else 1)
