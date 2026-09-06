"""Dedicated mayapy process: bulk geometry, transaction and Qt integration."""
import os,sys,time,unittest
from unittest.mock import patch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6 import QtCore,QtWidgets
app=QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
import maya.utils
from mesh_sentinel.model import Report,Settings
from mesh_sentinel.engine import Inspector
from mesh_sentinel.maya_bridge import snapshot,mesh_paths
from mesh_sentinel import bulk_repair
from mesh_sentinel.bulk_repair import repair_all,BulkOptions

class BulkTests(unittest.TestCase):
    def setUp(self): cmds.file(new=True,force=True); cmds.undoInfo(state=True)
    def polygon(self):
        return cmds.polyCreateFacet(p=[(0,0,0),(2,0,0),(3,1,0),(1,3,0),(0,1,0)],ch=False)[0]
    def report(self,rules):
        settings=Settings(enabled=tuple(rules))
        return Report('test',settings,[Inspector(settings).inspect(snapshot(p)) for p in mesh_paths('scene')])
    def test_two_meshes_one_undo(self):
        a=self.polygon(); b=self.polygon(); report=self.report(['ngon','concave'])
        changed,log=repair_all(report,BulkOptions(triangulate=True))
        self.assertEqual(len(changed),2)
        self.assertEqual((cmds.polyEvaluate(a,face=True),cmds.polyEvaluate(b,face=True)),(3,3))
        cmds.undo()
        for result in report.meshes: self.assertEqual(snapshot(result.path).fingerprint,result.fingerprint)
        cmds.redo(); self.assertEqual(cmds.polyEvaluate(b,face=True),3)
    def test_stale_later_target_is_atomic(self):
        a=self.polygon(); b=self.polygon(); report=self.report(['ngon'])
        cmds.move(1,0,0,b,relative=True)
        with self.assertRaisesRegex(RuntimeError,'再検査'): repair_all(report)
        self.assertEqual(cmds.polyEvaluate(a,face=True),1)
    def test_failure_rolls_back_all_targets(self):
        self.polygon(); self.polygon(); report=self.report(['ngon'])
        real=bulk_repair.apply_edit; calls=[]
        def fail(plan,**kwargs):
            calls.append(plan.path)
            real(plan,**kwargs)
            if len(calls)==2: raise RuntimeError('Injected second-target failure')
        with patch.object(bulk_repair,'apply_edit',side_effect=fail):
            with self.assertRaises(RuntimeError): repair_all(report,BulkOptions(triangulate=True))
        for result in report.meshes: self.assertEqual(snapshot(result.path).fingerprint,result.fingerprint)
    def test_duplicate_faces_keep_one(self):
        a=cmds.polyPlane(sx=1,sy=1,ch=False)[0]; b=cmds.duplicate(a)[0]
        obj=cmds.polyUnite(a,b,ch=False)[0]
        changed,log=repair_all(self.report(['duplicate_face']))
        self.assertEqual(len(changed),1)
        self.assertEqual(cmds.polyEvaluate(obj,face=True),1)
    def test_delete_default_off_and_whole_mesh_guard(self):
        a=cmds.polyCreateFacet(p=[(0,0,0),(100,0,0),(0,.00001,0)],ch=False)[0]
        report=self.report(['sliver'])
        self.assertTrue(report.meshes[0].findings)
        self.assertFalse(repair_all(report)[0])
        changed,log=repair_all(report,BulkOptions(delete=True))
        self.assertFalse(changed); self.assertTrue(any('全面削除' in s for s in log))
        self.assertEqual(cmds.polyEvaluate(a,face=True),1)
    def test_fill_before_triangulation(self):
        obj=cmds.polyCylinder(subdivisionsAxis=5,ch=False)[0]
        path=cmds.listRelatives(obj,shapes=True,fullPath=True)[0]
        data=snapshot(path); cap=next(i for i,vs in enumerate(data.faces) if len(vs)==5)
        cmds.delete(obj+'.f[{}]'.format(cap))
        report=self.report(['boundary','ngon'])
        changed,log=repair_all(report,BulkOptions(triangulate=True))
        result=self.report(['boundary','ngon'])
        self.assertTrue(changed)
        self.assertFalse(any(r.findings for r in result.meshes))
    def test_ui_all_results_despite_filter_and_refresh(self):
        from mesh_sentinel.ui import InspectorWindow
        from mesh_sentinel.bulk_ui import BulkRepairDialog
        directory=os.path.abspath(sys.argv[1]); os.makedirs(directory,exist_ok=True)
        store=QtCore.QSettings(directory+'/bulk.ini',QtCore.QSettings.IniFormat); store.clear()
        w=InspectorWindow(settings_store=store); w.show()
        a=self.polygon(); b=self.polygon(); cmds.select(a,b)
        for key,c in w.checks.items(): c.setChecked(key=='ngon')
        def settle():
            end=time.monotonic()+30
            while True:
                app.processEvents(); time.sleep(.01)
                if time.monotonic()>end: raise AssertionError(w.status.text())
                if not (w.worker or w.extracting or w.live.worker or w.live.timer.isActive()):
                    for _ in range(30): app.processEvents(); time.sleep(.01)
                    if not (w.live.worker or w.live.timer.isActive()): return
        try:
            with patch.object(maya.utils,'executeDeferred',side_effect=lambda f:QtCore.QTimer.singleShot(0,f)):
                w.scan(); settle(); w.search.setText('no matching rows')
                self.assertTrue(w.repair_all_btn.isEnabled())
                w.auto_review.setChecked(False)
                d=BulkRepairDialog(w); d.show()
                d.triangulate.setChecked(True)
                self.assertIn(w.report.meshes[1].path,d.summary.toPlainText())
                d.apply_button.click(); settle()
                self.assertFalse(any(r.findings for r in w.report.meshes))
                self.assertFalse(w.auto_review.isChecked())
                self.assertFalse(d.apply_button.isEnabled())
                d.close(); cmds.undo()
                self.assertEqual((cmds.polyEvaluate(a,face=True),cmds.polyEvaluate(b,face=True)),(1,1))
        finally:
            w.close(); app.processEvents(); w.live.dispose(); w.hover.dispose()

try:
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BulkTests))
finally: maya.standalone.uninitialize()
sys.exit(0 if result.wasSuccessful() else 1)
