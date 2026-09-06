"""Pure-Python regression tests; no Maya/Qt required."""
import unittest
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import MagicMock, patch
from mesh_sentinel.uv_topology import analyze_uv_topology as analyze
from mesh_sentinel import uv_repair as repair


class TopologyTests(unittest.TestCase):
    def test_single_quad(self):
        self.assertFalse(analyze([[0,1,2,3]],[[0,1,2,3]]).nonmanifold_uvs)

    def test_connected_quads(self):
        faces=[[0,1,4,3],[1,2,5,4]]
        self.assertFalse(analyze(faces,faces).nonmanifold_uvs)

    def test_ngon_is_not_triangulated(self):
        face=list(range(9)); before=face[:]
        self.assertFalse(analyze([face],[face]).nonmanifold_uvs)
        self.assertEqual(face,before)

    def test_closed_fan(self):
        faces=[[0,1,2],[0,2,3],[0,3,4],[0,4,1]]
        self.assertFalse(analyze(faces,faces).nonmanifold_uvs)

    def test_open_fan(self):
        faces=[[0,1,2],[0,2,3],[0,3,4]]
        self.assertFalse(analyze(faces,faces).nonmanifold_uvs)

    def test_bowtie_fans(self):
        faces=[[0,1,2,3],[0,4,5,6]]
        result=analyze(faces,faces)
        self.assertEqual(result.nonmanifold_uvs,(0,))
        self.assertEqual(result.affected_faces,(0,1))

    def test_shared_uv_on_disconnected_geometry(self):
        result=analyze([[0,1,2,3],[4,5,6,7]],[[0,1,2,3],[0,4,5,6]])
        self.assertEqual(result.nonmanifold_uvs,(0,))

    def test_separate_uv_shells_are_valid(self):
        self.assertFalse(analyze([[0,1,2,3],[0,4,5,6]],[[0,1,2,3],[4,5,6,7]]).nonmanifold_uvs)

    def test_partial_seam_with_shared_tip(self):
        faces=[[0,1,2],[0,2,3],[0,3,4],[0,4,1]]
        uvs=[[0,1,2],[0,2,3],[0,3,4],[0,4,5]]
        self.assertFalse(analyze(faces,uvs).nonmanifold_uvs)

    def test_three_faces_share_uv_edge(self):
        faces=[[0,1,2],[1,0,3],[0,1,4]]
        self.assertEqual(analyze(faces,faces).nonmanifold_uvs,(0,1))

    def test_same_direction_edge(self):
        faces=[[0,1,2],[0,1,3]]
        self.assertEqual(analyze(faces,faces).nonmanifold_uvs,(0,1))

    def test_uv_edge_joining_unrelated_geometry(self):
        self.assertEqual(analyze([[0,1,2],[3,4,5]],[[0,1,2],[1,0,3]]).nonmanifold_uvs,(0,1))

    def test_repeated_uv_corner(self):
        self.assertIn(0,analyze([[0,1,2,3]],[[0,1,0,2]]).nonmanifold_uvs)

    def test_missing_mapping(self):
        result=analyze([[0,1,2],[3,4,5]],[[],[3,4,5]])
        self.assertEqual(result.missing_faces,(0,))
        self.assertFalse(result.nonmanifold_uvs)

    def test_invalid_counts(self):
        with self.assertRaises(ValueError): analyze([[0,1,2]],[])


class TransactionTests(unittest.TestCase):
    """Check orchestration with fakes, NOT Maya geometry or native undo."""
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.cmds=MagicMock(); self.om=MagicMock()
        self.om.MDagPath.getAllPathsTo.return_value=[object()]
        self.cmds.referenceQuery.return_value=False
        self.cmds.lockNode.return_value=[False]
        self.cmds.u3dTopoValid.return_value=[]
        self.token=None
        def undo_info(**kwargs):
            if kwargs.get('openChunk'): self.token=kwargs['chunkName']
            if kwargs.get('state'): return True
            if kwargs.get('undoName'): return self.token
        self.cmds.undoInfo.side_effect=undo_info
        self.stack.enter_context(patch.object(repair,'_host',return_value=(self.cmds,self.om)))
        self.state=repair.UVState('|mesh','before','surface','map1',4,(4,),4,(),(),(),(),())
        self.inspect=self.stack.enter_context(patch.object(repair,'inspect_uv',return_value=self.state))
        self.coords={('map1',0,0):(0.,0.),('lightmap',0,0):(.5,.5)}
        self.snapshot=self.stack.enter_context(patch.object(repair,'_corner_uvs',return_value=self.coords))

    def test_repair_only_does_not_unfold(self):
        result=repair.repair_uv([self.state])
        self.assertFalse(result[0]['unfolded'])
        self.cmds.u3dUnfold.assert_not_called()
        self.cmds.polyTriangulate.assert_not_called()

    def test_optional_unfold(self):
        result=repair.repair_uv([self.state],unfold=True)
        self.assertTrue(result[0]['unfolded'])
        self.cmds.u3dUnfold.assert_called_once_with('|mesh.map[*]',iterations=1,pack=False)
        self.cmds.polyTriangulate.assert_not_called()

    def test_unfold_failure_requests_rollback(self):
        self.cmds.u3dUnfold.side_effect=RuntimeError('native failure')
        with self.assertRaisesRegex(RuntimeError,'native failure'): repair.repair_uv([self.state],unfold=True)
        self.cmds.undo.assert_called_once()

    def test_native_check_failure_prevents_unfold(self):
        self.cmds.u3dTopoValid.return_value='nonManifoldUV'
        with self.assertRaisesRegex(RuntimeError,'nonManifoldUV'): repair.repair_uv([self.state],unfold=True)
        self.cmds.u3dUnfold.assert_not_called()
        self.cmds.undo.assert_called_once()

    def test_coordinates_changed_during_repair_rolls_back(self):
        self.snapshot.side_effect=[self.coords,{('map1',0,0):(1.,0.)}]
        with self.assertRaisesRegex(RuntimeError,'既存UV座標'): repair.repair_uv([self.state])
        self.cmds.undo.assert_called_once()

    def test_unfold_cannot_change_other_uv_set(self):
        self.snapshot.side_effect=[self.coords,self.coords,{('map1',0,0):(1.,0.),('lightmap',0,0):(1.,1.)}]
        with self.assertRaisesRegex(RuntimeError,'既存UV座標'): repair.repair_uv([self.state],unfold=True)
        self.cmds.undo.assert_called_once()

    def test_unfold_can_move_active_uvs(self):
        self.snapshot.side_effect=[self.coords,self.coords,{('map1',0,0):(1.,0.),('lightmap',0,0):(.5,.5)}]
        self.assertTrue(repair.repair_uv([self.state],unfold=True)[0]['unfolded'])

    def test_no_cut_edges_still_attempts_reassignment(self):
        bad=replace(self.state,nonmanifold_uvs=(0,))
        after=replace(self.state,fingerprint='after')
        self.inspect.side_effect=[bad,bad,after]
        self.stack.enter_context(patch.object(repair,'_uv_cut_edges',return_value=[]))
        separate=self.stack.enter_context(patch.object(repair,'_separate_shared_uv_faces'))
        self.assertTrue(repair.repair_uv([bad])[0]['changed'])
        separate.assert_called_once_with(bad)

    def test_surface_changed_by_unfold_rolls_back(self):
        self.inspect.side_effect=[self.state,replace(self.state,surface='changed')]
        with self.assertRaisesRegex(RuntimeError,'展開後の検証'): repair.repair_uv([self.state],unfold=True)
        self.cmds.undo.assert_called_once()

    def test_stale_input_rejected(self):
        self.inspect.return_value=replace(self.state,fingerprint='changed')
        with self.assertRaisesRegex(RuntimeError,'形状・UV'): repair.repair_uv([self.state])
        self.cmds.u3dUnfold.assert_not_called()


if __name__=='__main__': unittest.main()
