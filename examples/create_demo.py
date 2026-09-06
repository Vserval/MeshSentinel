"""Create intentional defects in a new group, without clearing the user's scene."""


def create_mesh(name, points, faces):
    import maya.api.OpenMaya as om
    import maya.cmds as cmds
    fn = om.MFnMesh()
    transform = cmds.createNode("transform", name=name)
    selection = om.MSelectionList()
    selection.add(transform)
    fn.create([om.MPoint(*p) for p in points], [len(f) for f in faces], [v for f in faces for v in f],
              parent=selection.getDependNode(0))
    cmds.sets(transform, edit=True, forceElement="initialShadingGroup")
    return transform


def create_demo():
    import maya.cmds as cmds
    group = cmds.group(empty=True, name="MeshSentinel_Demo#")
    made = []
    cmds.undoInfo(openChunk=True, chunkName="MeshSentinelDemo")
    try:
        panel = create_mesh("Panel_ConcaveNgon", [(0,0,0),(3,0,0),(3,2,0),(1.5,.8,0),(0,2,0)], [(0,1,2,3,4)])
        made.append(panel)
        duplicate = create_mesh("Plate_DuplicateFaces", [(0,0,0),(2,0,0),(0,2,0)]*2, [(0,1,2),(3,4,5)])
        cmds.move(5,0,0,duplicate)
        made.append(duplicate)
        cross_mesh = create_mesh("Bracket_SelfIntersection", [(-1,-1,0),(1,-1,0),(0,1,0),(0,0,-1),(0,0,1),(.5,.5,0)], [(0,1,2),(3,4,5)])
        cmds.move(-4,0,0,cross_mesh)
        made.append(cross_mesh)
        sliver = create_mesh("Trim_Sliver", [(0,0,0),(3,0,0),(0,.008,0)], [(0,1,2)])
        cmds.move(0,-3,0,sliver)
        made.append(sliver)
        inward = cmds.polyCube(name="Housing_InwardNormals", constructionHistory=False)[0]
        cmds.polyNormal(inward, normalMode=0, userNormalMode=0, constructionHistory=False)
        cmds.move(5,-3,0,inward)
        made.append(inward)
        for obj in made:
            cmds.parent(obj, group)
        cmds.select(group, replace=True)
    finally:
        cmds.undoInfo(closeChunk=True)
    return group


if __name__ == "__main__":
    create_demo()
