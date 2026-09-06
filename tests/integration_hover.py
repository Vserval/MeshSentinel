"""Dedicated mayapy tests for accurate hover identity and occlusion."""
import os,sys,unittest
from unittest.mock import Mock
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
import maya.api.OpenMaya as om
from mesh_sentinel.hover_pick import HoverIndex,pick,scene_meshes,tooltip_html
from mesh_sentinel.model import Finding,Settings
from mesh_sentinel.maya_bridge import snapshot,mesh_paths
from mesh_sentinel.engine import Inspector
from mesh_sentinel.viewport import SESSION


class HoverTests(unittest.TestCase):
    def setUp(self):
        SESSION.clear()
        cmds.file(new=True,force=True)
        self.obj=cmds.polyPlane(width=4,height=4,subdivisionsX=1,subdivisionsY=1,constructionHistory=False)[0]
        self.path=cmds.ls(self.obj,dag=True,type='mesh',long=True)[0]

    def index(self,path=None,kind='f',ids=(0,)):
        path=path or self.path
        f=Finding('ngon',path,kind,list(ids),'テスト <説明>')
        return HoverIndex([f],[(path,kind,i) for i in ids])

    def query(self,index,origin=(0,5,0),direction=(0,-1,0),cursor=(0,0),meshes=None):
        return pick(index,om.MPoint(*origin),om.MVector(*direction),
                    lambda p:(p.x*100,p.z*100,True),cursor,
                    scene_meshes() if meshes is None else meshes)

    def test_front_face_and_tooltip_identity(self):
        hit=self.query(self.index())
        self.assertEqual(hit['path'],self.path)
        self.assertEqual(hit['issues'][0][:2],('f',0))
        self.assertIn('N-gon',tooltip_html(hit))
        self.assertIn('&lt;説明&gt;',tooltip_html(hit))

    def test_uninspected_front_object_blocks_rear_issue(self):
        front=cmds.polyPlane(width=4,height=4,constructionHistory=False)[0]
        cmds.move(0,2,0,front)
        self.assertIsNone(self.query(self.index()))

    def test_clean_front_face_does_not_fall_through_to_rear_same_mesh(self):
        rear=cmds.polyPlane(width=4,height=4,subdivisionsX=1,subdivisionsY=1,constructionHistory=False)[0]
        cmds.move(0,-2,0,rear)
        joined=cmds.polyUnite(self.obj,rear,constructionHistory=False)[0]
        path=cmds.ls(joined,dag=True,type='mesh',long=True)[0]
        self.assertIsNone(self.query(self.index(path,ids=(1,))))

    def test_backface_does_not_show_even_if_issue_exists(self):
        self.assertIsNone(self.query(self.index(),origin=(0,-5,0),direction=(0,1,0)))

    def test_background_has_no_tooltip(self):
        self.assertIsNone(self.query(self.index(),origin=(10,5,0)))

    def test_equal_depth_meshes_are_ambiguous(self):
        cmds.duplicate(self.obj)
        self.assertIsNone(self.query(self.index()))

    def test_hidden_occluder_does_not_block(self):
        cube=cmds.polyCube(constructionHistory=False)[0]
        cmds.move(0,2,0,cube)
        cmds.hide(cube)
        self.assertIsNotNone(self.query(self.index()))

    def test_instance_path_is_not_shared(self):
        instance=cmds.instance(self.obj)[0]
        cmds.move(6,0,0,instance)
        self.assertIsNone(self.query(self.index(),origin=(6,5,0),cursor=(600,0)))
        path='|'+instance+'|'+cmds.listRelatives(instance,shapes=True)[0]
        self.assertEqual(self.query(self.index(path),origin=(6,5,0),cursor=(600,0))['path'],path)

    def test_same_short_names_do_not_alias(self):
        other=cmds.duplicate(self.obj)[0]
        a,b=cmds.group(empty=True,name='left'),cmds.group(empty=True,name='right')
        first=cmds.parent(self.obj,a)[0]
        second=cmds.parent(other,b)[0]
        cmds.rename(first,'same')
        cmds.rename(second,'same')
        first_path=cmds.listRelatives('|left|same',shapes=True,fullPath=True)[0]
        cmds.move(6,0,0,'|right|same')
        self.assertIsNone(self.query(self.index(first_path),origin=(6,5,0),cursor=(600,0)))

    def test_edge_and_vertex_require_pixel_proximity(self):
        fn=om.MFnMesh(scene_meshes()[0][1])
        p=fn.getPoint(0,om.MSpace.kWorld)
        hit=self.query(self.index(kind='vtx',ids=(0,)),origin=(p.x,5,p.z),cursor=(p.x*100,p.z*100))
        self.assertEqual(hit['issues'][0][:2],('vtx',0))
        self.assertIsNone(self.query(self.index(kind='vtx',ids=(0,))))
        a,b=[fn.getPoint(v,om.MSpace.kWorld) for v in fn.getEdgeVertices(0)]
        p=om.MPoint((a.x+b.x)/2,0,(a.z+b.z)/2)
        hit=self.query(self.index(kind='e',ids=(0,)),origin=(p.x,5,p.z),cursor=(p.x*100,p.z*100))
        self.assertEqual(hit['issues'][0][:2],('e',0))

    def test_truncated_or_filtered_components_not_indexed(self):
        finding=Finding('ngon',self.path,'f',[0],'')
        self.assertIsNone(self.query(HoverIndex([finding],[])))

    def test_filtered_view_uses_exact_instance_paths(self):
        front=cmds.polyPlane(width=4,height=4,constructionHistory=False)[0]
        cmds.move(0,2,0,front)
        selected=om.MSelectionList()
        selected.add(self.obj)
        view=Mock()
        view.viewIsFiltered.return_value=True
        view.filteredObjectList.return_value=selected
        hit=self.query(self.index(),meshes=scene_meshes(view))
        self.assertEqual(hit['path'],self.path)

    def test_hover_never_changes_selection(self):
        cmds.select(self.obj+'.vtx[1]')
        before=cmds.ls(selection=True,long=True)
        self.query(self.index())
        self.assertEqual(cmds.ls(selection=True,long=True),before)

    def test_session_index_clears_with_overlay(self):
        data=snapshot(self.path)
        result=Inspector(Settings()).inspect(data)
        SESSION.show(result.findings,[result])
        self.assertIsNotNone(SESSION.hover_index)
        SESSION.clear()
        self.assertIsNone(SESSION.hover_index)

    def test_filter_change_on_same_component_updates_tooltip_rule(self):
        data=snapshot(self.path)
        result=Inspector(Settings()).inspect(data)
        first=Finding('ngon',self.path,'f',[0],'first')
        second=Finding('concave',self.path,'f',[0],'second')
        SESSION.show([first],[result])
        SESSION.show([second],[result])
        hit=self.query(SESSION.hover_index)
        self.assertEqual(hit['issues'][0][2].rule,'concave')


if __name__=='__main__':
    try:
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(HoverTests))
    finally:
        SESSION.clear()
        maya.standalone.uninitialize()
    sys.exit(not result.wasSuccessful())
