"""Run only in a dedicated mayapy process."""
import os,sys,unittest
from unittest.mock import patch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
import maya.api.OpenMaya as om
from mesh_sentinel.maya_bridge import snapshot,mesh_paths,select_findings
from mesh_sentinel.model import Finding,Settings
from mesh_sentinel.engine import Inspector
from mesh_sentinel.repair import plan_edit,apply_edit,checked_mesh

class Repairs(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True,force=True); cmds.undoInfo(state=True); cmds.currentUnit(linear='cm')
    def inspect(self,obj):
        cmds.select(obj); self.mesh=snapshot(mesh_paths()[0]); self.result=Inspector(Settings()).inspect(self.mesh)
        return self.mesh
    def plan(self,rule,kind,ids,action,chosen=None):
        f=Finding(rule,self.mesh.path,kind,ids,'test')
        return plan_edit(f,ids if chosen is None else chosen,action,self.mesh,Settings())
    def polygon(self):
        return cmds.polyCreateFacet(p=[(0,0,0),(2,0,0),(3,1,0),(1,3,0),(0,1,0)],ch=False)[0]
    def test_selection_switches_mode_hilite_and_mask(self):
        if cmds.about(batch=True): self.skipTest('Selection masks require Maya GUI; covered by GUI suite')
        obj=cmds.polyCube()[0]; self.inspect(obj)
        cmds.selectMode(object=True); cmds.selectType(polymeshVertex=True,polymeshFace=False)
        select_findings([Finding('normals',self.mesh.path,'f',[1],'')],[self.result])
        self.assertTrue(cmds.selectMode(q=True,component=True))
        self.assertTrue(cmds.selectType(q=True,polymeshFace=True))
        self.assertFalse(cmds.selectType(q=True,polymeshVertex=True))
        self.assertIn('|'+obj,cmds.ls(hilite=True,long=True))
        selection=om.MGlobal.getActiveSelectionList(); dag,comp=selection.getComponent(0)
        self.assertEqual(dag.fullPathName(),self.mesh.path)
        self.assertEqual(list(om.MFnSingleIndexedComponent(comp).getElements()),[1])
    def test_mixed_component_masks(self):
        if cmds.about(batch=True): self.skipTest('Selection masks require Maya GUI; covered by GUI suite')
        self.inspect(cmds.polyCube()[0])
        select_findings([Finding('normals',self.mesh.path,'f',[1],''),Finding('boundary',self.mesh.path,'e',[2],'')],[self.result])
        self.assertTrue(cmds.selectType(q=True,meshComponents=True))
        # Maya groups different component kinds under one DAG list entry.
        self.assertEqual({s.rsplit('.',1)[1] for s in cmds.ls(selection=True,flatten=True)}, {'f[1]','e[2]'})
    def test_triangulate_individual_and_undo(self):
        a=self.polygon(); b=cmds.duplicate(a)[0]; self.inspect(a)
        before=snapshot(cmds.listRelatives(b,shapes=True,fullPath=True)[0])
        apply_edit(self.plan('ngon','f',[0],'triangulate'))
        self.assertEqual(cmds.polyEvaluate(a,face=True),3)
        self.assertEqual(snapshot(before.path).fingerprint,before.fingerprint)
        cmds.undo(); self.assertEqual(snapshot(self.mesh.path).fingerprint,self.mesh.fingerprint)
        cmds.redo(); self.assertEqual(cmds.polyEvaluate(a,face=True),3)
    def test_delete_only_chosen_face(self):
        a=cmds.polyCube()[0]; self.inspect(a)
        p=self.plan('duplicate_face','f',[0,1],'delete',[1]); self.assertEqual(p.ids,(1,))
        apply_edit(p); self.assertEqual(cmds.polyEvaluate(a,face=True),5)
        cmds.undo(); self.assertEqual(snapshot(self.mesh.path).fingerprint,self.mesh.fingerprint)
    def test_vertex_delete_expands_to_incident_faces(self):
        self.inspect(cmds.polyCube()[0]); p=self.plan('t_junction','vtx',[0],'delete')
        expected=tuple(i for i,vs in enumerate(self.mesh.faces) if 0 in vs)
        self.assertEqual(p.ids,expected); self.assertEqual(p.kind,'f')
        apply_edit(p); self.assertEqual(cmds.polyEvaluate(self.mesh.path,face=True),3)
    def test_delete_final_face_and_undo(self):
        self.inspect(self.polygon()); p=self.plan('ngon','f',[0],'delete')
        self.assertIn('全フェイス',p.explanation)
        apply_edit(p); self.assertFalse(cmds.objExists(self.mesh.path))
        cmds.undo(); self.assertEqual(snapshot(self.mesh.path).fingerprint,self.mesh.fingerprint)
    def test_fill_expands_whole_loop(self):
        a=cmds.polyCube()[0]; cmds.delete(a+'.f[0]'); self.inspect(a)
        ids=next(f.ids for f in self.result.findings if f.rule=='boundary' and f.component=='e')
        p=self.plan('boundary','e',ids,'fill',[ids[0]])
        self.assertEqual(len(p.ids),4); apply_edit(p)
        self.assertEqual(cmds.polyEvaluate(a,face=True),6)
        result=Inspector(Settings()).inspect(snapshot(self.mesh.path))
        self.assertFalse(any(f.rule=='boundary' for f in result.findings))
    def test_reverse_normals(self):
        a=cmds.polyCube()[0]; cmds.polyNormal(a,normalMode=0,ch=False); self.inspect(a)
        apply_edit(self.plan('normals','f',list(range(6)),'reverse'))
        self.assertFalse(any(f.rule=='normals' for f in Inspector(Settings()).inspect(snapshot(self.mesh.path)).findings))
    def test_reset_custom_normals(self):
        a=cmds.polyPlane(sx=1,sy=1,ch=False)[0]; cmds.polyNormalPerVertex(a+'.vtx[*]',xyz=(0,-1,0)); self.inspect(a)
        apply_edit(self.plan('normals','f',[0],'reset_normals'))
        self.assertEqual(snapshot(self.mesh.path).opposing_normals,())
    def test_merge_detected_partner_world_units(self):
        a=cmds.polyPlane(sx=1,sy=1,ch=False)[0]; b=cmds.duplicate(a)[0]
        obj=cmds.polyUnite(a,b,ch=False)[0]; self.inspect(obj)
        ids=next(f.ids for f in self.result.findings if f.rule=='duplicate_vertex')
        p=self.plan('duplicate_vertex','vtx',ids,'merge',[ids[0]])
        self.assertEqual(len(p.ids),2)
        cmds.currentUnit(linear='m'); apply_edit(p)
        self.assertEqual(cmds.polyEvaluate(obj,vertex=True),7)
    def test_collapse_zero_edge(self):
        a=cmds.polyCube(ch=False)[0]; cmds.xform(a+'.vtx[1]',ws=True,t=cmds.pointPosition(a+'.vtx[0]',w=True)); self.inspect(a)
        f=next(f for f in self.result.findings if f.rule=='zero_edge')
        p=plan_edit(f,[f.ids[0]],'collapse',self.mesh,Settings()); apply_edit(p)
        self.assertLess(cmds.polyEvaluate(a,vertex=True),8)
    def test_stale_plan_rejected_without_edit(self):
        a=self.polygon(); self.inspect(a); p=self.plan('ngon','f',[0],'triangulate')
        cmds.move(1,0,0,a,relative=True)
        with self.assertRaisesRegex(RuntimeError,'プレビュー'): apply_edit(p)
        self.assertEqual(cmds.polyEvaluate(a,face=True),1)
    def test_instance_rejected(self):
        a=self.polygon(); cmds.instance(a); self.inspect(a)
        with self.assertRaisesRegex(RuntimeError,'インスタンス'): apply_edit(self.plan('ngon','f',[0],'triangulate'))
    def test_undo_disabled_rejected(self):
        self.inspect(self.polygon()); p=self.plan('ngon','f',[0],'triangulate'); cmds.undoInfo(state=False)
        with self.assertRaisesRegex(RuntimeError,'Undo'): apply_edit(p)
    def test_failed_command_does_not_undo_user_work(self):
        a=self.polygon(); self.inspect(a); p=self.plan('ngon','f',[0],'triangulate')
        marker=cmds.createNode('transform',name='userWork')
        with patch.object(cmds,'polyTriangulate',side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError): apply_edit(p)
        self.assertTrue(cmds.objExists(marker))
    def test_partial_command_rolls_back(self):
        a=self.polygon(); self.inspect(a); p=self.plan('ngon','f',[0],'triangulate')
        original=cmds.polyTriangulate
        def fail(*args,**kwargs): original(*args,**kwargs); raise RuntimeError('after edit')
        with patch.object(cmds,'polyTriangulate',side_effect=fail):
            with self.assertRaises(RuntimeError): apply_edit(p)
        self.assertEqual(snapshot(self.mesh.path).fingerprint,self.mesh.fingerprint)

try:
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Repairs))
finally:
    maya.standalone.uninitialize()
sys.exit(0 if result.wasSuccessful() else 1)
