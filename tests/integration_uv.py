"""Dedicated mayapy: no-triangulation UV repair and native Unfold validation."""
import os,sys,unittest
from unittest.mock import patch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6 import QtCore,QtWidgets
app=QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as c
import maya.api.OpenMaya as om
from mesh_sentinel.uv_repair import inspect_uv,repair_uv

class UVTests(unittest.TestCase):
    def setUp(self): c.file(new=True,force=True); c.undoInfo(state=True)
    def uv_corners(self,path,name):
        sl=om.MSelectionList(); sl.add(path); fn=om.MFnMesh(sl.getDagPath(0))
        u,v=fn.getUVs(name); counts,ids=fn.getAssignedUVs(name)
        return tuple(counts),tuple((u[i],v[i]) for i in ids)
    def bowtie(self):
        fn=om.MFnMesh()
        fn.create(om.MPointArray([om.MPoint(*p) for p in [(0,0,0),(1,0,0),(1,1,0),(0,1,0),(-1,0,0),(-1,-1,0),(0,-1,0)]]),
                  [4,4],[0,1,2,3,0,4,5,6])
        return om.MDagPath.getAPathTo(fn.object()).fullPathName()
    def test_geometry_quads_and_positions_preserved_unfold(self):
        path=self.bowtie(); before=inspect_uv(path)
        self.assertTrue(before.nonmanifold_vertices)
        with patch.object(c,'polyTriangulate',side_effect=AssertionError('No triangulation permitted')):
            results=repair_uv([before])
        after=inspect_uv(path)
        self.assertEqual(after.face_sizes,(4,4)); self.assertEqual(before.surface,after.surface)
        self.assertFalse(after.nonmanifold_vertices or after.nonmanifold_uvs)
        self.assertTrue(results[0]['unfold_topology_valid'])
        c.polyProjection(path+'.f[*]',type='Planar',mapDirection='z',constructionHistory=True)
        self.assertFalse(c.u3dTopoValid(path+'.map[*]',type=True))
        c.u3dUnfold(path+'.map[*]',iterations=1,pack=False)
        self.assertEqual(inspect_uv(path).surface,before.surface)
    def test_undo_restores_topology_and_uv(self):
        path=self.bowtie(); before=inspect_uv(path); repair_uv([before]); c.undo()
        self.assertEqual(inspect_uv(path).fingerprint,before.fingerprint)
    def test_uv_only_nonmanifold(self):
        fn=om.MFnMesh(); fn.create(om.MPointArray([om.MPoint(*p) for p in [(0,0,0),(1,0,0),(1,1,0),(0,1,0),(3,0,0),(4,0,0),(4,1,0),(3,1,0)]]),
                                 [4,4],[0,1,2,3,4,5,6,7])
        path=om.MDagPath.getAPathTo(fn.object()).fullPathName()
        fn.setUVs([0,1,1,0,2,2,1],[0,0,1,1,0,1,1]); fn.assignUVs([4,4],[0,1,2,3,0,4,5,6])
        before=inspect_uv(path)
        self.assertFalse(before.nonmanifold_vertices); self.assertTrue(before.nonmanifold_uvs)
        coordinates=self.uv_corners(path,'map1')
        repair_uv([before]); after=inspect_uv(path)
        self.assertEqual(after.vertices,before.vertices); self.assertEqual(after.surface,before.surface)
        self.assertFalse(after.nonmanifold_uvs)
        self.assertEqual(self.uv_corners(path,'map1'),coordinates)
        c.undo(); self.assertEqual(inspect_uv(path).fingerprint,before.fingerprint)
    def test_current_uv_set_only_and_existing_uv_positions(self):
        path=self.bowtie()
        c.polyProjection(path+'.f[*]',type='Planar',mapDirection='z')
        base=self.uv_corners(path,'map1')
        c.polyUVSet(path,create=True,uvSet='lightmap')
        c.polyUVSet(path,currentUVSet=True,uvSet='lightmap')
        before=inspect_uv(path); repair_uv([before]); after=inspect_uv(path)
        self.assertEqual(after.uv_set,'lightmap'); self.assertFalse(after.missing_faces)
        self.assertEqual(self.uv_corners(path,'map1'),base)
        c.undo(); self.assertEqual(inspect_uv(path).fingerprint,before.fingerprint)
    def test_partial_uv_mapping_preserves_mapped_face(self):
        obj=c.polyPlane(sx=2,sy=1)[0]; path=c.listRelatives(obj,shapes=True,fullPath=True)[0]
        c.polyMapDel(path+'.f[1]',constructionHistory=True)
        before=inspect_uv(path); base=self.uv_corners(path,'map1')[1]
        self.assertEqual(before.missing_faces,(1,))
        repair_uv([before]); after=inspect_uv(path)
        self.assertFalse(after.missing_faces)
        self.assertEqual(self.uv_corners(path,'map1')[1][:4],base)
    def test_stale_uv_preview_rejected(self):
        obj=c.polyPlane(sx=1,sy=1)[0]; path=c.listRelatives(obj,shapes=True,fullPath=True)[0]
        before=inspect_uv(path); c.polyEditUV(path+'.map[0]',relative=True,uValue=.1)
        with self.assertRaisesRegex(RuntimeError,'形状・UV'): repair_uv([before])
    def test_failed_solver_check_rolls_back(self):
        path=self.bowtie(); before=inspect_uv(path)
        c.loadPlugin('Unfold3D',quiet=True)
        with patch.object(c,'u3dTopoValid',return_value=['nonManifoldUV']):
            with self.assertRaisesRegex(RuntimeError,'事前検証'): repair_uv([before])
        self.assertEqual(inspect_uv(path).fingerprint,before.fingerprint)
    def test_missing_uv_and_split_opt_out(self):
        path=self.bowtie(); before=inspect_uv(path)
        with self.assertRaisesRegex(RuntimeError,'UV未割当'): repair_uv([before],create_missing=False)
        with self.assertRaisesRegex(RuntimeError,'形状自体'): repair_uv([before],split_geometry=False)
        self.assertEqual(inspect_uv(path).fingerprint,before.fingerprint)
    def test_bulk_defaults_do_not_triangulate(self):
        from mesh_sentinel.bulk_repair import BulkOptions,action_for
        self.assertIsNone(action_for('ngon',BulkOptions()))
        self.assertIsNone(action_for('concave',BulkOptions()))
    def test_repair_and_unfold_transaction(self):
        path=self.bowtie()
        c.polyProjection(path+'.f[*]',type='Planar',mapDirection='z',constructionHistory=True)
        before=inspect_uv(path)
        with patch.object(c,'polyTriangulate',side_effect=AssertionError('No triangulation')):
            result=repair_uv([before],unfold=True)
        self.assertTrue(result[0]['unfolded'])
        self.assertEqual(inspect_uv(path).surface,before.surface)
        self.assertFalse(c.u3dTopoValid(path+'.map[*]',type=True))
        c.undo()
        self.assertEqual(inspect_uv(path).fingerprint,before.fingerprint)

    def test_native_unfold_exception_rolls_back_repair(self):
        path=self.bowtie(); before=inspect_uv(path)
        c.loadPlugin('Unfold3D',quiet=True)
        with patch.object(c,'u3dUnfold',side_effect=RuntimeError('simulated unfold failure')):
            with self.assertRaisesRegex(RuntimeError,'simulated unfold failure'):
                repair_uv([before],unfold=True)
        self.assertEqual(inspect_uv(path).fingerprint,before.fingerprint)

    def test_uv_diagnostic_without_native_uv_report(self):
        from mesh_sentinel import uv_repair as module
        fn=om.MFnMesh()
        fn.create(om.MPointArray([om.MPoint(*p) for p in [(0,0,0),(1,0,0),(1,1,0),(0,1,0),(3,0,0),(4,0,0),(4,1,0),(3,1,0)]]),
                  [4,4],[0,1,2,3,4,5,6,7])
        path=om.MDagPath.getAPathTo(fn.object()).fullPathName()
        fn.setUVs([0,1,1,0,2,2,1],[0,0,1,1,0,1,1])
        fn.assignUVs([4,4],[0,1,2,3,0,4,5,6])
        coordinates=self.uv_corners(path,'map1')
        native=module._ids
        def without_uv_report(path,flag,kind):
            return () if flag in ('nonManifoldUVs','nonManifoldUVEdges') else native(path,flag,kind)
        with patch.object(module,'_ids',side_effect=without_uv_report):
            before=inspect_uv(path)
            self.assertIn(0,before.nonmanifold_uvs)
            repair_uv([before])
            self.assertFalse(inspect_uv(path).nonmanifold_uvs)
        self.assertEqual(self.uv_corners(path,'map1'),coordinates)
        self.assertFalse(c.u3dTopoValid(path+'.map[*]',type=True))

    def test_dialog_without_inspection_report(self):
        from mesh_sentinel.ui import InspectorWindow
        from mesh_sentinel.uv_ui import UVRepairDialog
        directory=os.path.abspath(sys.argv[1]); os.makedirs(directory,exist_ok=True)
        store=QtCore.QSettings(directory+'/uv.ini',QtCore.QSettings.IniFormat); store.clear()
        w=InspectorWindow(settings_store=store); w.show()
        path=self.bowtie(); d=UVRepairDialog(w,[path]); d.show()
        try:
            self.assertIsNone(w.report); self.assertTrue(w.uv_repair_btn.isEnabled())
            d.apply_button.click()
            self.assertIn('修復完了',d.status.text())
            self.assertFalse(inspect_uv(path).nonmanifold_vertices)
            self.assertEqual(inspect_uv(path).face_sizes,(4,4))
        finally:
            d.close(); w.close(); app.processEvents(); w.live.dispose(); w.hover.dispose()

try: result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(UVTests))
finally: maya.standalone.uninitialize()
sys.exit(0 if result.wasSuccessful() else 1)
