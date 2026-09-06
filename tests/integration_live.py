"""Incremental repair/Undo regression; dedicated mayapy process only."""
import os,sys,time,tempfile,threading
from unittest.mock import patch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6 import QtWidgets,QtCore
app=QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
import maya.utils
from mesh_sentinel.ui import InspectorWindow
from mesh_sentinel.viewport import SESSION

def settle(w):
    deadline=time.monotonic()+30
    while True:
        app.processEvents()
        if time.monotonic()>deadline:
            raise AssertionError('Timed out: '+w.status.text())
        if not (w.worker or w.extracting or w.live.worker or w.live.timer.isActive()):
            for _ in range(50):
                app.processEvents(); time.sleep(.005)
            if not (w.live.worker or w.live.timer.isActive()):
                break
        time.sleep(.005)

def count(w):
    return sum(len(f.ids) for m in w.report.meshes for f in m.findings)

def run(directory):
    w=InspectorWindow(settings_store=QtCore.QSettings(directory+'/test.ini',QtCore.QSettings.IniFormat))
    w.show()
    for key,check in w.checks.items(): check.setChecked(key=='ngon')
    a=cmds.polyCreateFacet(p=[(0,0,0),(1,0,0),(2,1,0),(1,2,0),(0,1,0)],ch=False)[0]
    b=cmds.duplicate(a)[0]
    cmds.move(4,0,0,b)
    cmds.select(a,b)
    w.scan(); settle(w)
    assert count(w)==2 and len(w.live.targets)==2
    untouched=w.report.meshes[1]
    cmds.hilite(a); cmds.selectMode(component=True); cmds.selectType(polymeshFace=True)
    cmds.select(a+'.f[0]'); settle(w)
    assert count(w)==2 and SESSION.payload is not None
    cmds.polyTriangulate(a,ch=False); settle(w)
    assert count(w)==1,(count(w),w.status.text())
    assert w.report.meshes[1] is untouched,'Unedited mesh was rescanned'
    cmds.undo(); settle(w)
    assert count(w)==2,'Undo did not restore issue'
    cmds.redo(); settle(w)
    assert count(w)==1
    cmds.polyTriangulate(b,ch=False); settle(w)
    assert count(w)==0 and SESSION.payload is None and len(w.live.targets)==2
    cmds.undo(); settle(w)
    assert count(w)==1 and SESSION.payload is not None
    b=cmds.rename(b,'renamedTarget'); settle(w)
    assert count(w)==1 and 'renamedTarget' in w.report.meshes[1].path
    cmds.delete(b); settle(w)
    assert len(w.report.meshes)==1 and count(w)==0
    cmds.undo(); settle(w)
    assert len(w.report.meshes)==2 and count(w)==1
    cmds.polyTriangulate(b,ch=False); settle(w)
    assert count(w)==0,'Restored mesh no longer watched'
    # Editing while analysis is running must discard the older snapshot.
    from mesh_sentinel.engine import Inspector
    original=Inspector.inspect
    started,release=threading.Event(),threading.Event()
    def delayed(self,mesh):
        started.set()
        if not release.wait(10): raise RuntimeError('Test barrier timeout')
        return original(self,mesh)
    with patch.object(Inspector,'inspect',delayed):
        cmds.undo()
        deadline=time.monotonic()+10
        while not started.is_set():
            app.processEvents(); time.sleep(.005)
            assert time.monotonic()<deadline
        cmds.redo()
        release.set()
        settle(w)
    assert count(w)==0,'Stale worker result was published'
    # A failed read must remain incomplete, never be reported as fixed.
    from mesh_sentinel import maya_bridge
    original_snapshot=maya_bridge.snapshot
    def failed_snapshot(path):
        if 'renamedTarget' in path: raise RuntimeError('Injected read failure')
        return original_snapshot(path)
    with patch.object(maya_bridge,'snapshot',failed_snapshot):
        w.live.request_all(); settle(w)
        assert not w.report.complete
        assert w.report.meshes[1].checks['ngon']=='failed'
    w.live.request_all(); settle(w)
    assert w.report.complete and count(w)==0
    # Watch clean meshes with automatic review disabled/re-enabled.
    w.auto_review.setChecked(False)
    cmds.undo(); settle(w)
    assert count(w)==0
    w.auto_review.setChecked(True); settle(w)
    assert count(w)==1
    w.close(); app.processEvents(); w.live.dispose(); w.hover.dispose()
    print('LIVE PASS: face selection, incremental repair, untouched result identity, Undo/Redo, clean monitoring, rename, delete, restore and edit, stale worker rejection, failed read, toggle, shutdown')

try:
    if len(sys.argv)>1:
        directory=os.path.abspath(sys.argv[1])
        os.makedirs(directory,exist_ok=True)
        with patch.object(maya.utils,'executeDeferred',side_effect=lambda f:QtCore.QTimer.singleShot(0,f)):
            run(directory)
    else:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(maya.utils,'executeDeferred',side_effect=lambda f:QtCore.QTimer.singleShot(0,f)):
                run(directory)
finally:
    maya.standalone.uninitialize()
