"""Viewport plug-in/session tests. Dedicated mayapy process required."""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaRender as omr
import maya.utils
from mesh_sentinel import viewport
from mesh_sentinel.maya_bridge import mesh_paths, snapshot
from mesh_sentinel.model import Settings, Finding
from mesh_sentinel.engine import Inspector
from mesh_sentinel.overlay_data import build_batches


class ViewportTests(unittest.TestCase):
    def setUp(self):
        # Mayapy has no GUI idle event loop. Queue deferred work explicitly;
        # native GUI regression tests exercise Maya's actual idle dispatcher.
        self.deferred = []
        self.defer_patch = patch('maya.utils.executeDeferred',side_effect=self.deferred.append)
        self.defer_patch.start()
        self.addCleanup(self.defer_patch.stop)
        viewport.SESSION.clear()
        cmds.file(new=True,force=True)
        self.obj = cmds.polyPlane(subdivisionsX=1,subdivisionsY=1,constructionHistory=False)[0]
        cmds.select(self.obj)
        self.mesh = snapshot(mesh_paths()[0])
        self.result = Inspector(Settings()).inspect(self.mesh)

    def tearDown(self):
        viewport.SESSION.clear()

    def show(self):
        return viewport.SESSION.show(self.result.findings,[self.result])

    def drain(self):
        while self.deferred:
            self.deferred.pop(0)()

    def test_show_without_source_or_selection_changes(self):
        before = cmds.ls(selection=True,long=True)
        original = self.mesh.signature()
        payload = self.show()
        self.assertEqual(payload["displayed"],4)
        self.assertEqual(len(cmds.ls(type=viewport.NODE_TYPE)),1)
        self.assertEqual(snapshot(self.mesh.path).signature(),original)
        self.assertEqual(cmds.ls(selection=True,long=True),before)
        self.assertTrue(viewport.SESSION.payload)

    def test_clear_deletes_only_helper(self):
        original_nodes = set(cmds.ls(long=True))
        self.show()
        viewport.SESSION.clear()
        self.assertEqual(set(cmds.ls(long=True)),original_nodes)
        self.assertEqual(viewport.SESSION.callbacks,[])

    def test_display_options_reuse_node_and_snapshot(self):
        self.show()
        self.drain()
        handle = viewport.SESSION.handle.hashCode()
        with patch('mesh_sentinel.maya_bridge.snapshot', wraps=snapshot) as read:
            viewport.SESSION.show(self.result.findings,[self.result],xray=True,labels=True)
            read.assert_not_called()
        self.assertEqual(viewport.SESSION.handle.hashCode(),handle)
        self.assertTrue(viewport.SESSION.payload['xray'])

    def test_default_is_depth_tested(self):
        self.assertFalse(self.show()['xray'])

    def test_labels_hide_behind_other_meshes_and_follow_moves(self):
        from mesh_sentinel.sentinel_overlay_plugin import label_visible, label_occluders
        point, eye = om.MPoint(0,0,0), (0,5,0)
        self.assertTrue(label_visible(point,eye,None,label_occluders()))
        blocker = cmds.polyCube(constructionHistory=False)[0]
        cmds.move(0,2,0,blocker)
        self.assertFalse(label_visible(point,eye,None,label_occluders()))
        self.assertFalse(label_visible(point,eye,(0,1,0),label_occluders()))
        cmds.move(10,2,0,blocker)
        self.assertTrue(label_visible(point,eye,None,label_occluders()))
        cmds.move(0,2,0,blocker)
        cmds.hide(blocker)
        self.assertTrue(label_visible(point,eye,None,label_occluders()))

    def test_save_does_not_serialize_overlay(self):
        self.show()
        descriptor,path = tempfile.mkstemp(prefix="sentinel-",suffix=".ma")
        os.close(descriptor)
        try:
            cmds.file(rename=path)
            cmds.file(save=True,type="mayaAscii",force=True)
            with open(path,encoding="utf-8",errors="replace") as stream:
                text = stream.read()
            self.assertNotIn("createNode meshSentinelOverlay",text)
            self.assertNotIn("meshSentinelOverlay_TEMP",text)
            self.assertNotIn('requires "sentinel_overlay_plugin"',text)
        finally:
            os.remove(path)

    def test_stale_snapshot_rejected(self):
        cmds.move(1,0,0,self.obj)
        with self.assertRaisesRegex(RuntimeError,"再検査"):
            self.show()
        self.assertFalse(cmds.ls(type=viewport.NODE_TYPE))

    def test_transform_edit_invalidates_after_evaluation(self):
        self.show()
        cmds.move(2,0,0,self.obj)
        self.drain()
        self.assertIsNone(viewport.SESSION.payload)

    def test_same_time_notification_keeps_overlay(self):
        self.show()
        viewport.SESSION.time_changed(None)
        self.drain()
        self.assertIsNotNone(viewport.SESSION.payload)

    def test_actual_frame_change_invalidates(self):
        self.show()
        cmds.currentTime(cmds.currentTime(query=True)+1)
        viewport.SESSION.time_changed(None)
        self.drain()
        self.assertIsNone(viewport.SESSION.payload)

    def test_vertex_edit_invalidates_after_evaluation(self):
        self.show()
        cmds.move(1,0,0,self.obj+".vtx[0]",relative=True)
        self.drain()
        self.assertIsNone(viewport.SESSION.payload)

    def test_selection_and_component_mode_keep_overlay(self):
        self.show()
        for action in (lambda:cmds.select(clear=True),lambda:cmds.select(self.obj),
                       lambda:cmds.select(self.obj+'.vtx[0]'),lambda:cmds.hilite(self.obj),
                       lambda:cmds.selectMode(component=True),lambda:cmds.selectMode(object=True)):
            action()
            self.drain()
            self.assertIsNotNone(viewport.SESSION.payload)

    def test_display_only_dirty_does_not_invalidate(self):
        self.show()
        cmds.setAttr(self.mesh.path+'.displayColors',True)
        self.drain()
        self.assertIsNotNone(viewport.SESSION.payload)
        self.assertEqual(snapshot(self.mesh.path).fingerprint,self.mesh.fingerprint)

    def test_dirty_without_geometry_change_keeps_overlay(self):
        self.show()
        cmds.dgdirty(self.mesh.path,allPlugs=True)
        self.drain()
        self.assertIsNotNone(viewport.SESSION.payload)

    def test_repeated_notifications_coalesce_one_snapshot(self):
        self.show()
        with patch('mesh_sentinel.maya_bridge.snapshot', wraps=snapshot) as read:
            for _ in range(10):
                viewport.SESSION.request_validation()
            self.drain()
            self.assertEqual(read.call_count,1)
        self.assertIsNotNone(viewport.SESSION.payload)

    def test_queued_old_validation_cannot_clear_new_overlay(self):
        self.show()
        viewport.SESSION.request_validation()
        self.show()
        self.drain()
        self.assertIsNotNone(viewport.SESSION.payload)
        self.assertEqual(len(cmds.ls(type=viewport.NODE_TYPE)),1)

    def test_history_edit_still_invalidates(self):
        viewport.SESSION.clear()
        obj,history = cmds.polyPlane(subdivisionsX=1,subdivisionsY=1,constructionHistory=True)
        cmds.select(obj)
        self.mesh = snapshot(mesh_paths()[0])
        self.result = Inspector(Settings()).inspect(self.mesh)
        self.show()
        cmds.setAttr(history+'.width',3.)
        self.drain()
        self.assertIsNone(viewport.SESSION.payload)

    def test_new_scene_cleans_callbacks(self):
        self.show()
        cmds.file(new=True,force=True)
        self.assertIsNone(viewport.SESSION.payload)
        self.assertEqual(viewport.SESSION.callbacks,[])

    def test_draw_batches_include_faces_edges_points(self):
        findings = [Finding("ngon",self.mesh.path,"f",[0],"test"),
                    Finding("zero_edge",self.mesh.path,"e",[0],"test"),
                    Finding("duplicate_vertex",self.mesh.path,"vtx",[1],"test")]
        viewport.SESSION.show(findings,[self.result],xray=True,labels=True)
        # Maya's plug-in loader does not expose its module in sys.modules.
        # Importing this file normally defines the same drawing class without registering it again.
        from mesh_sentinel import sentinel_overlay_plugin as module
        shape = viewport.SESSION.handle.object()
        draw = module.OverlayDraw(shape)
        data = draw.prepareForDraw(None,None,None,None)
        manager = MagicMock()
        draw.addUIDrawables(None,manager,None,data)
        primitives = {c.args[0] for c in manager.mesh.call_args_list}
        self.assertEqual(primitives,{omr.MUIDrawManager.kPoints,omr.MUIDrawManager.kLines,omr.MUIDrawManager.kTriangles})
        manager.beginDrawInXray.assert_called_once()
        manager.endDrawInXray.assert_called_once()
        manager.beginDrawable.assert_called_once_with(omr.MUIDrawManager.kNonSelectable)
        self.assertGreater(manager.text.call_count,0)
        points = data.batches[0]['points']
        viewport.SESSION.show(findings,[self.result],xray=True,labels=False)
        data = draw.prepareForDraw(None,None,None,data)
        self.assertIs(data.batches[0]['points'],points)
        manager.reset_mock()
        draw.addUIDrawables(None,manager,None,data)
        manager.text.assert_not_called()
        viewport.SESSION.clear()
        manager.reset_mock()
        draw.addUIDrawables(None,manager,None,data)
        manager.mesh.assert_not_called()

    def test_component_priority_and_display_limit(self):
        fs = [Finding("boundary",self.mesh.path,"e",[0],"test"),Finding("zero_edge",self.mesh.path,"e",[0],"test")]
        data = build_batches(fs,[self.mesh])
        self.assertEqual(data["total"],1)
        self.assertEqual(data["batches"][0]["severity"],"error")
        limited = build_batches([Finding("ngon",self.mesh.path,"f",[0],"test")],[self.mesh],limit=1)
        self.assertTrue(limited["truncated"])
        self.assertEqual(limited["displayed"],0)


if __name__ == "__main__":
    try:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ViewportTests))
    finally:
        viewport.SESSION.clear()
        maya.standalone.uninitialize()
    sys.exit(0 if result.wasSuccessful() else 1)
