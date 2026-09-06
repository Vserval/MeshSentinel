"""Dedicated mayapy: dialog scope, Undo, forced refresh with auto review off."""
import os,sys,time
from unittest.mock import patch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6 import QtCore,QtWidgets
app=QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
import maya.utils
from mesh_sentinel.ui import InspectorWindow
from mesh_sentinel.repair_ui import RepairDialog

def settle(w):
    end=time.monotonic()+30
    while True:
        app.processEvents(); time.sleep(.01)
        if time.monotonic()>end: raise AssertionError(w.status.text())
        if not (w.worker or w.extracting or w.live.worker or w.live.timer.isActive()):
            for _ in range(30): app.processEvents(); time.sleep(.01)
            if not (w.live.worker or w.live.timer.isActive()): return
def issues(w): return [f for r in w.report.meshes for f in r.findings]
def run(directory):
    os.makedirs(directory,exist_ok=True)
    store=QtCore.QSettings(directory+'/repair.ini',QtCore.QSettings.IniFormat); store.clear()
    w=InspectorWindow(settings_store=store); w.show()
    for key,c in w.checks.items(): c.setChecked(key=='ngon')
    a=cmds.polyCreateFacet(p=[(0,0,0),(2,0,0),(3,1,0),(1,3,0),(0,1,0)],ch=False)[0]
    b=cmds.duplicate(a)[0]; cmds.select(a,b); w.scan(); settle(w)
    assert len(issues(w))==2
    d=RepairDialog(w,issues(w)[0]); d.show()
    assert d.plan.ids==(0,) and d.plan.action=='triangulate'
    d.apply_button.click(); settle(w)
    assert len(issues(w))==1 and cmds.polyEvaluate(a,face=True)==3
    assert cmds.polyEvaluate(b,face=True)==1
    cmds.undo(); settle(w)
    assert len(issues(w))==2 and cmds.polyEvaluate(a,face=True)==1
    cmds.redo(); settle(w)
    assert len(issues(w))==1
    w.auto_review.setChecked(False)
    d=RepairDialog(w,issues(w)[0]); d.show(); d.operation.setCurrentIndex(d.operation.findData('delete'))
    assert d.plan.ids==(0,) and d.plan.kind=='f'
    d.apply_button.click(); settle(w)
    assert not issues(w) and len(w.report.meshes)==1,repr(([(r.path,r.checks) for r in w.report.meshes],
        [(f.rule,f.ids) for f in issues(w)],list(w.live.targets),w.live.once,list(w.live.dirty),list(w.live.stale)))
    assert not w.auto_review.isChecked() and not w.live.targets
    cmds.undo(); cmds.select(b); w.scan(); settle(w)
    assert len(issues(w))==1
    d=RepairDialog(w,issues(w)[0]); d.show()
    cmds.move(1,0,0,b,relative=True)
    d.apply_button.click()
    assert 'プレビュー' in d.message.text() and cmds.polyEvaluate(b,face=True)==1
    d.close(); w.close(); app.processEvents(); w.live.dispose(); w.hover.dispose()
    print('REPAIR UI PASS: exact component preview, apply, untouched second mesh, Undo/Redo, delete final face, refresh with auto review off, stale dialog rejection, shutdown')
try:
    with patch.object(maya.utils,'executeDeferred',side_effect=lambda f:QtCore.QTimer.singleShot(0,f)):
        run(os.path.abspath(sys.argv[1]))
finally:
    maya.standalone.uninitialize()
