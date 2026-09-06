"""Offscreen UI acceptance test. Run only in a dedicated mayapy process."""
import os
import sys
import time
import tempfile
import json
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtWidgets, QtCore, QtGui, QtTest

# Mayapy creates a QGuiApplication during standalone.initialize. Widgets need
# QApplication instead, so this must be constructed first in this test process.
app = QtWidgets.QApplication([])
if os.name == "nt":
    for font in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "YuGothM.ttc", "YuGothB.ttc"):
        QtGui.QFontDatabase.addApplicationFont(os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", font))
app.setFont(QtGui.QFont("Segoe UI", 10))

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
from create_demo import create_demo
from mesh_sentinel.ui import InspectorWindow


def drain(window):
    deadline = time.monotonic() + 60
    while window.extracting or window.worker is not None:
        app.processEvents()
        if time.monotonic() > deadline:
            window.cancel()
            raise TimeoutError("Scan timed out")
        time.sleep(.005)
    app.processEvents()


def run(directory):
    os.makedirs(directory, exist_ok=True)
    settings = QtCore.QSettings(os.path.join(directory, "ui.ini"), QtCore.QSettings.IniFormat)
    settings.clear()
    settings.setValue('overlay_xray','true')  # Old default must migrate off.
    window = InspectorWindow(settings_store=settings)
    def raise_error(exc):
        raise RuntimeError(str(exc))
    window._error = raise_error
    window.show()
    assert not window.overlay_xray.isChecked()
    app.processEvents()
    window.scale_combo.setCurrentIndex(window.scale_combo.findData(100))
    app.processEvents()
    baseline_font = window.scan_btn.font().pixelSize()
    window.scale_combo.setCurrentIndex(window.scale_combo.findData(200))
    app.processEvents()
    assert window.scan_btn.font().pixelSize() == baseline_font*2
    assert window.canvas.minimumWidth() == 1840
    assert window.canvas_scroll.horizontalScrollBar().maximum() > 0
    window.scale_combo.setCurrentIndex(window.scale_combo.findData(125))
    assert settings.value("ui_scale",type=int) == 125
    app.processEvents()
    assert window.metric_labels["meshes"].font().pixelSize() == 38
    window.grab().save(os.path.join(directory, "ready.png"))
    group = create_demo()
    window.scan_btn.click()
    drain(window)
    assert window.report.complete and len(window.report.meshes) == 5
    assert window.scan_btn.isEnabled() and window.export_btn.isEnabled()
    assert window.metric_labels["error"].text() == "3"
    assert window.metric_labels["warning"].text() == "8"
    assert window.metric_labels["review"].text() == "1"
    QtTest.QTest.qWait(250)
    from mesh_sentinel.viewport import SESSION
    assert SESSION.payload is not None, window.overlay_status.text()
    window.overlay_mode.setCurrentIndex(1)
    for action in window.export_btn.menu().actions():
        path = os.path.join(directory, "report." + action.text().lower())
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(path, "")):
            action.trigger()
        assert os.path.getsize(path) > 100
    with open(os.path.join(directory, "report.json"), encoding="utf-8") as f:
        assert json.load(f)["complete"]
    window.tree.topLevelItem(0).child(0).setSelected(True)
    app.processEvents()
    assert window.select_btn.isEnabled()
    window.select_btn.click()
    assert cmds.ls(selection=True, flatten=True)
    QtTest.QTest.qWait(250)
    assert SESSION.payload is not None, window.overlay_status.text()
    assert SESSION.payload["total"] == 2
    window.overlay_clear_btn.click()
    assert SESSION.payload is None
    window.grab().save(os.path.join(directory, "results.png"))
    window.search.setText("凹N-gon")
    app.processEvents()
    visible = [window.tree.topLevelItem(i) for i in range(window.tree.topLevelItemCount())
               if not window.tree.topLevelItem(i).isHidden()]
    assert len(visible) == 1
    window.search.clear()
    window.severity.setCurrentIndex(1)
    app.processEvents()
    for i in range(window.tree.topLevelItemCount()):
        parent = window.tree.topLevelItem(i)
        for j in range(parent.childCount()):
            item = parent.child(j)
            if not item.isHidden():
                assert item.text(1) == "エラー"
    window.severity.setCurrentIndex(0)
    window.tabs.setCurrentIndex(1)
    app.processEvents()
    window.grab().save(os.path.join(directory, "settings.png"))
    window.tabs.setCurrentIndex(0)
    cmds.select(group)
    window.scan_btn.click()
    window.cancel_btn.click()
    drain(window)
    assert window.report.cancelled and not window.report.complete
    window.close()
    app.processEvents()
    assert not window.hover.timer.isActive()
    window.show()
    app.processEvents()
    assert window.hover.timer.isActive()
    window.close()
    app.processEvents()
    # Hot-reload must recreate an already loaded window and leave no overlay.
    # Keep the singleton's normal settings isolated to this test directory.
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
    QtCore.QSettings.setPath(QtCore.QSettings.IniFormat,QtCore.QSettings.UserScope,directory)
    import mesh_sentinel
    from mesh_sentinel import ui
    ui._window = window
    # Avoid platform-level settings fallback into the user's registry.
    with patch.object(QtCore, "QSettings", return_value=settings):
        replacement = mesh_sentinel.reload_ui()
    from mesh_sentinel import viewport
    assert viewport.SESSION is not SESSION
    assert SESSION.payload is None and not SESSION.callbacks
    assert callable(viewport.SESSION.request_validation)
    assert replacement is not window and replacement.isVisible()
    assert window.hover.disposed
    assert replacement.overlay_hover.isChecked()
    assert replacement.scale_combo.currentData() == 125
    replacement.close()
    app.processEvents()
    print("UI PASS: scaling, scroll, persistence, launch, scan, metrics, 3 export menu actions, viewport modes, native selection, search, severity, cancellation, close, hot reload")


if __name__ == "__main__":
    try:
        with tempfile.TemporaryDirectory(prefix="sentinel-ui-") as directory:
            run(directory)
    finally:
        maya.standalone.uninitialize()
