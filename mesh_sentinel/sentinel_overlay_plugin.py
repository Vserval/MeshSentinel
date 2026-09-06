"""Internal Python VP2 plug-in, loaded on demand by mesh_sentinel.viewport."""
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui
import maya.api.OpenMayaRender as omr
from mesh_sentinel import viewport
from mesh_sentinel.overlay_data import COLORS, visible_indices

TYPE_ID = om.MTypeId(0x0007F71A)  # Local-use ID; helper nodes are never serialized.
CLASSIFICATION = "drawdb/geometry/meshSentinelOverlay"
REGISTRANT = "MeshSentinelOverlayDraw"


def label_occluders():
    """Read native mesh handles only when the optional ID labels are enabled."""
    import maya.cmds as cmds
    occluders = []
    for path in cmds.ls(dag=True,type="mesh",long=True,allPaths=True,visible=True) or []:
        selection = om.MSelectionList()
        selection.add(path)
        dag = selection.getDagPath(0)
        fn = om.MFnMesh(dag)
        if fn.isIntermediateObject:
            continue
        box = fn.boundingBox
        box.transformUsing(dag.inclusiveMatrix())
        occluders.append((fn, fn.autoUniformGridParams(), box))
    return occluders


def label_visible(point, eye, toward_eye, occluders):
    """Maya UI text ignores depth; test its anchor against native mesh rays."""
    origin = om.MPoint(*eye)
    if toward_eye is not None:
        toward = om.MVector(*toward_eye)
        origin = point + toward * ((origin-point)*toward)
    ray = point-origin
    distance = ray.length()
    if distance <= 1e-8:
        return False
    # Avoid rejecting the label's own surface with the single-precision API.
    margin = max(1e-5, distance*1e-5)
    ray_box = om.MBoundingBox(origin,point)
    for fn, accelerator, box in occluders:
        if not box.intersects(ray_box):
            continue
        hit = fn.closestIntersection(om.MFloatPoint(origin),om.MFloatVector(ray/distance),
                                     om.MSpace.kWorld,max(distance-margin,1e-8),False,
                                     accelParams=accelerator)
        if hit is not None and hit[1] < distance-margin:
            return False
    return True


def maya_useNewAPI():
    pass


class OverlayNode(omui.MPxLocatorNode):
    @staticmethod
    def creator():
        return OverlayNode()

    @staticmethod
    def initialize():
        pass

    def isBounded(self):
        return False

    def excludeAsLocator(self):
        return True


class OverlayData(om.MUserData):
    def __init__(self):
        super().__init__(False)
        self.revision = -1
        self.batches = []
        self.xray = False
        self.labels = False
        self.camera_key = None
        self.geometry = None


class OverlayDraw(omr.MPxDrawOverride):
    def __init__(self, obj):
        # Camera-dependent culling must also update during orbiting. Cached
        # MPointArrays survive refreshes; only visible indices change per view.
        super().__init__(obj, None, True)

    @staticmethod
    def creator(obj):
        return OverlayDraw(obj)

    def supportedDrawAPIs(self):
        return omr.MRenderer.kOpenGL | omr.MRenderer.kOpenGLCoreProfile | omr.MRenderer.kDirectX11

    def isBounded(self, obj_path, camera_path):
        return False

    def hasUIDrawables(self):
        return True

    def prepareForDraw(self, obj_path, camera_path, frame_context, old_data):
        data = old_data if isinstance(old_data, OverlayData) else OverlayData()
        if data.revision != viewport.SESSION.revision:
            data.revision = viewport.SESSION.revision
            payload = viewport.SESSION.payload
            data.camera_key = None
            geometry = payload["batches"] if payload else None
            if geometry is not data.geometry:
                data.batches = []
            rebuild = geometry is not data.geometry
            data.geometry = geometry
            if payload:
                data.xray, data.labels = payload["xray"], payload["labels"]
                for batch in payload["batches"] if rebuild else ():
                    data.batches.append({"color": COLORS[batch["severity"]],
                        "points": om.MPointArray([om.MPoint(*p) for p in batch["points"]]),
                        "lines": om.MPointArray([om.MPoint(*p) for p in batch["lines"]]),
                        "triangles": om.MPointArray([om.MPoint(*p) for p in batch["triangles"]]),
                        "labels": tuple((om.MPoint(*p), text) for p, text in batch["labels"]),
                        "source": batch, "indices": None, "visible_labels": (), "front_labels": ()})
        if data.batches:
            if data.xray:
                key, eye, direction = "xray", None, None
            else:
                camera = om.MFnCamera(camera_path)
                point = camera.eyePoint(om.MSpace.kWorld)
                eye = (point.x,point.y,point.z)
                vector = -camera.viewDirection(om.MSpace.kWorld)
                direction = (vector.x,vector.y,vector.z) if camera.isOrtho else None
                key = (eye, direction)
            if data.camera_key != key:
                data.camera_key = key
                for batch in data.batches:
                    if data.xray:
                        batch["indices"] = None
                        batch["visible_labels"] = batch["labels"]
                    else:
                        visible = visible_indices(batch["source"],eye,direction)
                        batch["indices"] = {name: om.MUintArray(visible[name]) for name in ("points","lines","triangles")}
                        batch["front_labels"] = tuple(batch["labels"][i] for i in visible["labels"])
            if data.labels and not data.xray:
                # Recheck each refresh: an unrelated occluding mesh can move
                # without changing the inspected mesh or the active camera.
                occluders = label_occluders()
                for batch in data.batches:
                    batch["visible_labels"] = tuple((point,text) for point,text in batch["front_labels"]
                                                    if label_visible(point,eye,direction,occluders))
        return data

    def addUIDrawables(self, obj_path, draw_manager, frame_context, data):
        if not data or not data.batches or viewport.SESSION.payload is None:
            return
        draw_manager.beginDrawable(omr.MUIDrawManager.kNonSelectable)
        try:
            draw_manager.setLineWidth(1.5)
            draw_manager.setPointSize(6.)
            # Keep diagnostic edges visible alongside active face/component
            # highlighting. This is a depth bias, not an X-ray pass.
            draw_manager.setDepthPriority(omr.MRenderItem.sActiveWireDepthPriority)
            if data.xray:
                draw_manager.beginDrawInXray()
            try:
                for batch in data.batches:
                    rgb = batch["color"]
                    for name, mode, alpha in (("triangles",omr.MUIDrawManager.kTriangles,.14),
                                              ("lines",omr.MUIDrawManager.kLines,.95),
                                              ("points",omr.MUIDrawManager.kPoints,1.)):
                        indices = batch["indices"][name] if batch["indices"] is not None else None
                        if len(batch[name]) and (indices is None or len(indices)):
                            draw_manager.setColor(om.MColor((*rgb, alpha)))
                            draw_manager.mesh(mode, batch[name], index=indices)
                    if data.labels:
                        draw_manager.setFontSize(11)
                        draw_manager.setColor(om.MColor((*rgb, 1.)))
                        for point, text in batch["visible_labels"]:
                            draw_manager.text(point, text)
            finally:
                if data.xray:
                    draw_manager.endDrawInXray()
        finally:
            draw_manager.endDrawable()


def initializePlugin(obj):
    plugin = om.MFnPlugin(obj, "Mesh Sentinel", "1.7.0-rc1", "Any")
    plugin.registerNode(viewport.NODE_TYPE, TYPE_ID, OverlayNode.creator, OverlayNode.initialize,
                        om.MPxNode.kLocatorNode, CLASSIFICATION)
    try:
        omr.MDrawRegistry.registerDrawOverrideCreator(CLASSIFICATION, REGISTRANT, OverlayDraw.creator)
    except Exception:
        plugin.deregisterNode(TYPE_ID)
        raise


def uninitializePlugin(obj):
    viewport.SESSION.clear()
    omr.MDrawRegistry.deregisterDrawOverrideCreator(CLASSIFICATION, REGISTRANT)
    om.MFnPlugin(obj).deregisterNode(TYPE_ID)
