"""Debounced incremental review; original targets survive clean results and Undo."""
from functools import partial
import datetime
try:
    from PySide6 import QtCore,QtWidgets
except ImportError:
    from PySide2 import QtCore,QtWidgets
from .model import Report,MeshResult


class LiveReview(QtCore.QObject):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner
        self.worker=None
        self.targets={}
        self.results={}
        self.callbacks=[]
        self.dirty=set()
        self.stale=set()
        self.reading=False
        self.epoch=0
        self.revision=0
        self.disposed=False
        self.once=False
        self.timer=QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(700)
        self.timer.timeout.connect(self.check)
        self.exit_callback=None
        import maya.api.OpenMaya as om
        self.exit_callback=om.MSceneMessage.addCallback(om.MSceneMessage.kMayaExiting,self.dispose)
        QtWidgets.QApplication.instance().aboutToQuit.connect(self.dispose)
        owner.destroyed.connect(self.dispose)

    def reset(self,*args):
        import maya.api.OpenMaya as om
        self.epoch+=1
        self.timer.stop()
        if self.worker:
            self.worker.stop.set()
        for callback in self.callbacks:
            om.MMessage.removeCallback(callback)
        self.callbacks=[]
        self.targets={}
        self.results={}
        self.dirty=set()
        self.stale=set()
        self.once=False

    def dispose(self,*args):
        if self.disposed:
            return
        self.disposed=True
        self.reset()
        if self.worker:
            # The worker only owns Python snapshots; wait before Qt destroys it.
            self.worker.wait()
        if self.exit_callback is not None:
            import maya.api.OpenMaya as om
            om.MMessage.removeCallback(self.exit_callback)
            self.exit_callback=None

    def bind(self,report,once=False):
        import maya.api.OpenMaya as om
        if self.disposed:
            return
        self.reset()
        if not report or report.cancelled or (not self.owner.auto_review.isChecked() and not once):
            return
        self.once=once
        self.settings,self.scope=report.settings,report.scope
        self.base_errors=list(report.errors)
        for result in report.meshes:
            try:
                selection=om.MSelectionList()
                selection.add(result.path)
                dag=selection.getDagPath(0)
                key=result.path
                self.targets[key]=(dag,result.uuid,result.path)
                self.results[key]=result
                callback=partial(self.request,key)
                self.callbacks.append(om.MNodeMessage.addNodeDirtyPlugCallback(dag.node(),callback))
                self.callbacks.append(om.MNodeMessage.addNodePreRemovalCallback(dag.node(),callback))
                self.callbacks.append(om.MNodeMessage.addNameChangedCallback(dag.node(),callback))
                self.callbacks.append(om.MDagMessage.addWorldMatrixModifiedCallback(dag,callback))
                parent=om.MDagPath(dag)
                while parent.length()>1:
                    parent.pop()
                    self.callbacks.append(om.MNodeMessage.addNameChangedCallback(parent.node(),callback))
            except RuntimeError:
                continue
        for event in ('Undo','Redo','timeChanged'):
            self.callbacks.append(om.MEventMessage.addEventCallback(event,self.request_all))
        for event in (om.MSceneMessage.kBeforeNew,om.MSceneMessage.kBeforeOpen):
            self.callbacks.append(om.MSceneMessage.addCallback(event,self.reset))

    def request(self,key,*args):
        if self.reading or key not in self.targets:
            return
        self.dirty.add(key)
        self.revision+=1
        self.timer.start()

    def request_all(self,*args):
        if self.reading or not self.targets:
            return
        self.dirty.update(self.targets)
        self.revision+=1
        self.timer.start()

    def changed(self,paths):
        """Called after an overlay snapshot has confirmed a real change."""
        for key,target in self.targets.items():
            result=self.results.get(key)
            if target[2] in paths or (result and result.path in paths):
                self.request(key)
        self.stale.update(paths)
        self.owner.hover.hide()
        self.owner._update_overlay()

    def current_path(self,key):
        import maya.cmds as cmds
        dag,uuid,original=self.targets[key]
        try:
            path=dag.fullPathName()
            if cmds.objExists(path) and cmds.ls(path,uuid=True)==[uuid]:
                return path
        except RuntimeError:
            pass
        # Undo can restore the same UUID. Require the original/current path;
        # do not substitute a different instance of a shared shape.
        expected=self.results[key].path if key in self.results else original
        for path in cmds.ls(uuid,long=True,allPaths=True) or []:
            if path in (original,expected):
                return path
        return None

    def check(self):
        if (not self.targets or not self.owner.isVisible() or not (self.owner.auto_review.isChecked() or self.once)
                or self.owner.extracting or self.owner.worker is not None):
            return
        if self.worker is not None or QtWidgets.QApplication.mouseButtons()!=QtCore.Qt.NoButton:
            self.timer.start()
            return
        from .maya_bridge import snapshot
        pending,self.dirty=self.dirty,set()
        self.reading=True
        snapshots={}
        replacements={}
        try:
            for key in pending:
                old=self.results.get(key)
                path=self.current_path(key)
                if path is None:
                    if old:
                        replacements[key]=None
                        self.stale.add(old.path)
                    continue
                try:
                    current=snapshot(path)
                    if old and current.fingerprint==old.fingerprint and current.path==old.path:
                        self.stale.discard(old.path)
                        continue
                    snapshots[key]=current
                except (RuntimeError,ValueError) as exc:
                    failed=MeshResult(path,self.targets[key][1],'',0,0)
                    failed.checks={k:'failed' for k in self.settings.enabled}
                    failed.notes=['自動確認で読み取れませんでした: '+str(exc)]
                    replacements[key]=failed
                if old:
                    self.stale.add(old.path)
        finally:
            self.reading=False
        if not snapshots and not replacements:
            if self.once:
                self.reset()
            self.owner._details()
            self.owner._schedule_overlay()
            return
        self.owner.hover.hide()
        self.owner._update_overlay()
        if not snapshots:
            self.apply(replacements)
            return
        from .ui import ScanWorker
        self.pending_keys={m.path:key for key,m in snapshots.items()}
        self.pending_replacements=replacements
        self.run_epoch,self.run_revision=self.epoch,self.revision
        self.pending_snapshots=snapshots
        self.run_keys=set(pending)
        self.worker=ScanWorker(list(snapshots.values()),self.settings,self.scope,[],self)
        self.received_report=None
        self.worker.ready.connect(self.received)
        self.worker.finished.connect(self.finished)
        self.worker.start()
        self.owner.badge.setText('AUTO CHECK')
        self.owner.status.setText('編集を確認中：変更された {} メッシュを再検査しています…'.format(len(snapshots)))
        self.owner.select_btn.setEnabled(False)
        self.owner.frame_btn.setEnabled(False)
        self.owner.repair_btn.setEnabled(False)
        self.owner.repair_all_btn.setEnabled(False)

    def received(self,report):
        self.received_report=report

    def finished(self):
        worker,self.worker=self.worker,None
        worker.deleteLater()
        if self.run_epoch!=self.epoch:
            return
        report=self.received_report
        if self.run_revision!=self.revision or report is None or report.cancelled:
            self.dirty.update(self.run_keys)
            self.timer.start()
            return
        # Read again before publishing: edits during analysis must not apply
        # a result for old component IDs, even if a host notification was lost.
        from .maya_bridge import snapshot
        self.reading=True
        try:
            valid=all(self.current_path(key)==mesh.path and snapshot(mesh.path).fingerprint==mesh.fingerprint
                      for key,mesh in self.pending_snapshots.items())
        except (RuntimeError,ValueError):
            valid=False
        finally:
            self.reading=False
        if not valid:
            self.dirty.update(self.run_keys)
            self.timer.start()
            return
        replacements=dict(self.pending_replacements)
        replacements.update({self.pending_keys[result.path]:result for result in report.meshes})
        self.apply(replacements)
        if self.dirty:
            self.timer.start()

    def apply(self,replacements):
        selected=[]
        selected_meshes=[]
        for item in self.owner.tree.selectedItems():
            data=item.data(0,QtCore.Qt.UserRole)
            if hasattr(data,'rule'):
                selected.append((data.mesh,data.rule,data.component))
            elif isinstance(data,MeshResult):
                selected_meshes.append(data.uuid)
        for key,result in replacements.items():
            old=self.results.get(key)
            if old:
                self.stale.discard(old.path)
            if result is None:
                self.results.pop(key,None)
            else:
                self.results[key]=result
                self.stale.discard(result.path)
        report=Report(datetime.datetime.now(datetime.timezone.utc).isoformat(),self.settings,
                      meshes=[self.results[k] for k in self.targets if k in self.results],
                      errors=self.base_errors,scope=self.scope)
        self.owner._received(report)
        for i in range(self.owner.tree.topLevelItemCount()):
            parent=self.owner.tree.topLevelItem(i)
            data=parent.data(0,QtCore.Qt.UserRole)
            if isinstance(data,MeshResult) and data.uuid in selected_meshes:
                parent.setSelected(True)
            for j in range(parent.childCount()):
                item=parent.child(j)
                data=item.data(0,QtCore.Qt.UserRole)
                if hasattr(data,'rule') and (data.mesh,data.rule,data.component) in selected:
                    item.setSelected(True)
        self.owner._details()
        self.owner._schedule_overlay()
        if report.complete:
            count=len({(f.mesh,f.component,i) for r in report.meshes for f in r.findings for i in f.ids})
            self.owner.status.setText('自動確認完了：残り {:,} 箇所。解消した問題は表示から外しました。'.format(count))
        elif not report.meshes:
            self.owner.status.setText('検査対象のメッシュは削除されました。Undo時は再確認します。')
        if self.once:
            self.reset()
