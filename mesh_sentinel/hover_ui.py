"""Passive hover card. No mouse capture, event consumption, or selection edits."""
import time
try:
    from PySide6 import QtCore,QtGui,QtWidgets
    from shiboken6 import wrapInstance
except ImportError:
    from PySide2 import QtCore,QtGui,QtWidgets
    from shiboken2 import wrapInstance
from . import viewport
from .hover_pick import pick_view,tooltip_html


class HoverController(QtCore.QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.last_hit = None
        self.last_error = None
        self.position = None
        self.since = 0.
        self.disposed = False
        self.exit_callback = None
        self.card = QtWidgets.QLabel(owner,QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
        self.card.setObjectName('sentinelHoverCard')
        self.card.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.card.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.card.setFocusPolicy(QtCore.Qt.NoFocus)
        self.card.setTextFormat(QtCore.Qt.RichText)
        self.card.setWordWrap(True)
        self.card.hide()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        QtWidgets.QApplication.instance().aboutToQuit.connect(self.dispose)
        owner.destroyed.connect(self.dispose)
        import maya.api.OpenMaya as om
        self.exit_callback = om.MSceneMessage.addCallback(om.MSceneMessage.kMayaExiting,self.dispose)

    def dispose(self, *args):
        if self.disposed:
            return
        self.disposed = True
        self.timer.stop()
        self.hide()
        if self.exit_callback is not None:
            import maya.api.OpenMaya as om
            om.MMessage.removeCallback(self.exit_callback)
            self.exit_callback = None

    def hide(self):
        self.card.hide()
        self.last_hit = None

    def view_at(self, position):
        import maya.cmds as cmds
        import maya.api.OpenMayaUI as omui
        under = QtWidgets.QApplication.widgetAt(position)
        if under is None:
            return None
        for panel in cmds.getPanel(type='modelPanel') or []:
            try:
                view = omui.M3dView.getM3dViewFromModelPanel(panel)
                pointer = view.widget()
                if not pointer:
                    continue
                widget = wrapInstance(int(pointer),QtWidgets.QWidget)
            except RuntimeError:
                continue
            if widget.isVisible() and (under == widget or widget.isAncestorOf(under)):
                local = widget.mapFromGlobal(position)
                if widget.rect().contains(local):
                    return view,widget,local
        return None

    def tick(self):
        if self.disposed:
            return
        app = QtWidgets.QApplication
        session = viewport.SESSION
        if (not self.owner.isVisible() or not self.owner.overlay_hover.isChecked()
                or self.owner.worker is not None or self.owner.extracting
                or session.payload is None or session.hover_index is None or session.validation_pending
                or app.mouseButtons() != QtCore.Qt.NoButton
                or app.keyboardModifiers() & QtCore.Qt.AltModifier
                or app.activeModalWidget() is not None or app.activePopupWidget() is not None):
            self.hide()
            self.position = None
            return
        position = QtGui.QCursor.pos()
        if self.position is None or (position-self.position).manhattanLength()>2:
            self.hide()
            self.position = position
            self.since = time.monotonic()
            return
        if time.monotonic()-self.since < .3:
            return
        try:
            target = self.view_at(position)
            if target is None:
                self.hide()
                return
            view,widget,local = target
            sx,sy = view.portWidth()/max(1,widget.width()),view.portHeight()/max(1,widget.height())
            x,y = round(local.x()*sx),view.portHeight()-1-round(local.y()*sy)
            generation,revision = session.generation,session.revision
            hit = pick_view(session.hover_index,view,x,y,7.*max(sx,sy))
            if (session.generation != generation or session.revision != revision
                    or session.payload is None or session.validation_pending):
                hit = None
            if hit is None:
                self.hide()
                return
            self.last_error = None
            self.last_hit = hit
            scale = self.owner.scale_combo.currentData()/100.
            self.card.setStyleSheet('QLabel#sentinelHoverCard { background:#17232e; color:#e5edf5; '
                                   'border:1px solid #619989; border-radius:8px; padding:12px; '
                                   'font-family:"Segoe UI","Yu Gothic UI"; font-size:%dpx; }' % round(12*scale))
            self.card.setMaximumWidth(round(430*scale))
            self.card.setText(tooltip_html(hit))
            self.card.adjustSize()
            screen = QtGui.QGuiApplication.screenAt(position) or QtGui.QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            x = min(position.x()+20,area.right()-self.card.width())
            y = min(position.y()+24,area.bottom()-self.card.height())
            self.card.move(max(area.left(),x),max(area.top(),y))
            self.card.show()
        except (RuntimeError,ValueError,TypeError) as exc:
            # A deleted view/object must clear the previous card, never leave
            # a plausible but stale identification on screen.
            self.last_error = '{}: {}'.format(type(exc).__name__,exc)
            self.hide()
