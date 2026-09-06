"""Host-independent camera visibility regression tests."""
import unittest
from test_engine import mesh
from mesh_sentinel.model import Finding
from mesh_sentinel.overlay_data import build_batches, visible_indices


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.mesh = mesh([(0,0,0),(1,0,0),(0,1,0)],[(0,1,2)])

    def batch(self,kind,ids):
        return build_batches([Finding('boundary',self.mesh.path,kind,ids,'')],[self.mesh])['batches'][0]

    def test_front_back_all_component_types(self):
        for kind in ('f','e','vtx'):
            b = self.batch(kind,[0])
            front = visible_indices(b,(0,0,5))
            back = visible_indices(b,(0,0,-5))
            self.assertTrue(any(front[k] for k in ('triangles','lines','points')))
            self.assertFalse(any(back.values()))

    def test_orthographic_uses_direction_not_camera_position(self):
        b = self.batch('f',[0])
        self.assertTrue(visible_indices(b,(0,0,-5),(0,0,1))['triangles'])
        self.assertFalse(visible_indices(b,(0,0,5),(0,0,-1))['triangles'])

    def test_normal_face_has_no_center_marker(self):
        self.assertFalse(self.batch('f',[0])['points'])

    def test_degenerate_and_loose_vertices_remain_visible(self):
        self.mesh = mesh([(0,0,0),(1,0,0),(2,0,0),(3,0,0)],[(0,1,2)])
        self.assertTrue(visible_indices(self.batch('f',[0]),(0,0,-5))['points'])
        self.assertTrue(visible_indices(self.batch('vtx',[3]),(0,0,-5))['points'])

    def test_vertex_with_one_front_face_remains_visible(self):
        self.mesh = mesh([(0,0,0),(1,0,0),(0,1,0)],[(0,1,2),(2,1,0)])
        self.assertTrue(visible_indices(self.batch('vtx',[0]),(0,0,5))['points'])
        self.assertTrue(visible_indices(self.batch('vtx',[0]),(0,0,-5))['points'])


if __name__ == '__main__':
    unittest.main()
