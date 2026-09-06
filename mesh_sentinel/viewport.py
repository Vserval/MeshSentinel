"""Session-only Viewport 2.0 overlay; source geometry and colors are untouched."""
import os
import weakref

from .overlay_data import build_batches

NODE_TYPE = "meshSentinelOverlay"
PLUGIN_NAME = "sentinel_overlay_plugin"


class OverlaySession:
    def __init__(self):
        self.payload = None
        self.revision = 0
        self.handle = None
        self.root = None
        self.callbacks = []
        self.notice = None
        self.generation = 0
        self.expected = {}
        self.validation_pending = False
        self.validating = False
        self.time_seconds = None
        self.display_key = None
        self.hover_index = None
        self.refresh = None

    @staticmethod
    def _current_seconds():
        import maya.api.OpenMaya as om
        import maya.api.OpenMayaAnim as oma
        return oma.MAnimControl.currentTime().asUnits(om.MTime.kSeconds)

    def time_changed(self, *args):
        # Selection-mode changes can emit timeChanged at the same frame.
        if self.payload is None:
            return
        if self._current_seconds() != self.time_seconds:
            self.invalidate()
        else:
            self.request_validation()

    def request_validation(self, *args):
        """Dirty means 'needs evaluation', not 'geometry has changed'.

        Selection, component highlighting and Maya's UI can dirty mesh plugs.
        Coalesce notifications and read geometry only after the DG callback ends.
        Keep the overlay visible if the evaluated inspection snapshot is identical.
        """
        if self.payload is None or self.validation_pending or self.validating:
            return
        self.validation_pending = True
        generation = self.generation
        import maya.utils
        def validate():
            if generation != self.generation:
                return
            self.validation_pending = False
            if self.payload is None:
                return
            from .maya_bridge import snapshot
            self.validating = True
            changed_paths=[]
            try:
                for path,fingerprint in self.expected.items():
                    try:
                        if snapshot(path).fingerprint!=fingerprint:
                            changed_paths.append(path)
                    except (RuntimeError,ValueError):
                        changed_paths.append(path)
            finally:
                self.validating = False
            if changed_paths:
                callback=self.refresh() if self.refresh else None
                if callback:
                    callback(changed_paths)
                else:
                    self.invalidate()
        maya.utils.executeDeferred(validate)

    def _dirty(self,*args):
        if self.handle is not None and self.handle.isValid() and self.handle.isAlive():
            import maya.api.OpenMayaRender as omr
            omr.MRenderer.setGeometryDrawDirty(self.handle.object())

    def _notify(self, message):
        callback = self.notice() if self.notice else None
        if callback:
            callback(message)

    def invalidate(self, *args):
        if self.payload is None:
            return
        if self.refresh and self.refresh():
            self.request_validation()
            return
        self.payload = None
        self.revision += 1
        self._dirty()
        generation = self.generation
        import maya.utils
        def finish():
            if generation == self.generation:
                self._notify("形状・フレームの変更により表示を解除しました。再検査してください。")
                self.clear()
        maya.utils.executeDeferred(finish)

    def _scene_reset(self, *args):
        self._notify("シーンの切り替えにより表示を解除しました。")
        self.clear()

    def clear(self):
        import maya.api.OpenMaya as om
        self.generation += 1
        self.expected = {}
        self.validation_pending = False
        self.time_seconds = None
        self.display_key = None
        self.hover_index = None
        self.refresh = None
        self.payload = None
        self.revision += 1
        self._dirty()
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            om.MMessage.removeCallback(callback)
        root, self.root = self.root, None
        self.handle = None
        if root is not None and root.isValid() and root.isAlive():
            modifier = om.MDagModifier()
            modifier.deleteNode(root.object())
            modifier.doIt()
        self.notice = None

    def show(self, findings, results, xray=False, labels=False, notice=None, refresh=None):
        from .maya_bridge import _host, snapshot
        cmds, om = _host()
        display_key = (tuple((f.mesh,f.component,tuple(f.ids),f.severity,f.rule,f.message) for f in findings),
                       tuple((r.path,r.fingerprint) for r in results))
        # Settings changes reuse the existing helper and geometry. Any queued
        # geometry notification forces the full validation path below.
        if self.payload is not None and not self.validation_pending and self.display_key == display_key:
            self.payload.update(xray=bool(xray), labels=bool(labels))
            self.notice = weakref.WeakMethod(notice) if notice is not None else None
            self.refresh = weakref.WeakMethod(refresh) if refresh is not None else None
            self.revision += 1
            self._dirty()
            return self.payload
        self.clear()
        if not findings:
            return {"displayed": 0, "total": 0, "truncated": False}
        by_path = {r.path: r for r in results}
        snapshots, dags = [], []
        for path in sorted({f.mesh for f in findings}):
            if not cmds.objExists(path):
                raise RuntimeError("対象が削除・リネームされています。再検査してください。")
            current = snapshot(path)
            if current.fingerprint != by_path[path].fingerprint:
                raise RuntimeError("検査後に形状・変換・法線が変わっています。再検査してください。")
            snapshots.append(current)
            selection = om.MSelectionList()
            selection.add(path)
            dags.append(selection.getDagPath(0))
        payload = build_batches(findings, snapshots)
        payload.update(xray=bool(xray), labels=bool(labels))
        if not payload["batches"]:
            return payload
        plugin_path = os.path.join(os.path.dirname(__file__), "sentinel_overlay_plugin.py")
        if not cmds.pluginInfo(PLUGIN_NAME, query=True, loaded=True):
            cmds.loadPlugin(plugin_path, quiet=True)
        try:
            modifier = om.MDagModifier()
            root = modifier.createNode("transform")
            shape = modifier.createNode(NODE_TYPE, root)
            modifier.doIt()
            self.root, self.handle = om.MObjectHandle(root), om.MObjectHandle(shape)
            root_fn, shape_fn = om.MFnDependencyNode(root), om.MFnDependencyNode(shape)
            root_fn.setName("meshSentinelOverlay_TEMP#")
            shape_fn.setName("meshSentinelOverlayShape#")
            root_fn.setDoNotWrite(True)
            shape_fn.setDoNotWrite(True)
            root_fn.findPlug("hiddenInOutliner", False).setBool(True)
            for name in ("translate", "rotate", "scale", "shear"):
                root_fn.findPlug(name, False).isLocked = True
            # Never a selection target; no material/colorSet is attached.
            shape_fn.findPlug("overrideEnabled", False).setBool(True)
            shape_fn.findPlug("overrideDisplayType", False).setInt(2)
            self.payload = payload
            from .hover_pick import HoverIndex
            self.hover_index = HoverIndex(findings,payload['components'])
            self.display_key = display_key
            self.expected = {m.path: m.fingerprint for m in snapshots}
            self.time_seconds = self._current_seconds()
            self.revision += 1
            self.notice = weakref.WeakMethod(notice) if notice is not None else None
            self.refresh = weakref.WeakMethod(refresh) if refresh is not None else None
            watched = set()
            for dag in dags:
                if om.MObjectHandle(dag.node()).hashCode() not in watched:
                    watched.add(om.MObjectHandle(dag.node()).hashCode())
                    self.callbacks.append(om.MNodeMessage.addNodeDirtyPlugCallback(dag.node(), self.request_validation))
                    self.callbacks.append(om.MNodeMessage.addNodePreRemovalCallback(dag.node(), self.invalidate))
                    self.callbacks.append(om.MNodeMessage.addNameChangedCallback(dag.node(), self.invalidate))
                self.callbacks.append(om.MDagMessage.addWorldMatrixModifiedCallback(dag, self.request_validation))
            self.callbacks.append(om.MEventMessage.addEventCallback("timeChanged", self.time_changed))
            for event in ('SelectionChanged','SelectModeChanged','SelectTypeChanged'):
                if event in om.MEventMessage.getEventNames():
                    self.callbacks.append(om.MEventMessage.addEventCallback(event,self._dirty))
            self.callbacks.append(om.MSceneMessage.addCallback(om.MSceneMessage.kBeforeNew, self._scene_reset))
            self.callbacks.append(om.MSceneMessage.addCallback(om.MSceneMessage.kBeforeOpen, self._scene_reset))
            self._dirty()
        except Exception:
            self.clear()
            raise
        return payload


SESSION = OverlaySession()
