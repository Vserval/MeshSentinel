"""Per-window scaling without changing Maya's application-wide DPI/font settings."""
import re
from .ui import QtCore, QtWidgets


def scale_css(css, factor):
    # Qt stylesheets require integer pixel sizes for fonts and several metrics.
    return re.sub(r"(?<![\w#])([0-9]+(?:\.[0-9]+)?)px", lambda m: "{}px".format(round(float(m.group(1))*factor)), css)


class UiScaler:
    def __init__(self, window, canvas, stylesheet):
        self.window, self.canvas, self.stylesheet = window, canvas, stylesheet
        self.factor = 1.
        self.widgets = [(w, w.minimumSize(), w.maximumSize(), w.styleSheet())
                        for w in window.findChildren(QtWidgets.QWidget)]
        self.layouts = []
        for layout in window.findChildren(QtWidgets.QLayout):
            margin = layout.contentsMargins()
            self.layouts.append((layout, (margin.left(),margin.top(),margin.right(),margin.bottom()),layout.spacing()))

    def apply(self, percent, resize=True):
        factor = percent/100.
        self.window.setUpdatesEnabled(False)
        try:
            self.window.setStyleSheet(scale_css(self.stylesheet, factor))
            for widget, minimum, maximum, css in self.widgets:
                widget.setStyleSheet(scale_css(css, factor))
                widget.setMinimumSize(round(minimum.width()*factor), round(minimum.height()*factor))
                widget.setMaximumSize(round(maximum.width()*factor) if maximum.width() < 16777215 else 16777215,
                                      round(maximum.height()*factor) if maximum.height() < 16777215 else 16777215)
            for layout, margins, spacing in self.layouts:
                layout.setContentsMargins(*(round(m*factor) for m in margins))
                if spacing >= 0:
                    layout.setSpacing(round(spacing*factor))
            self.canvas.setMinimumSize(round(920*factor), round(780*factor))
            self.window.progress_bar.setFixedHeight(round(5*factor))
            self.window.tree.setIndentation(round(16*factor))
            for column, width in ((1,67),(2,46),(3,50),(4,70)):
                self.window.tree.setColumnWidth(column, round(width*factor))
            screen = self.window.screen() or QtWidgets.QApplication.primaryScreen()
            available = screen.availableGeometry()
            max_width, max_height = max(400,available.width()-40), max(350,available.height()-70)
            self.window.setMinimumSize(min(920,max_width), min(720,max_height))
            if resize and not self.window.isMaximized():
                self.window.resize(min(round(self.window.width()*factor/self.factor),max_width),
                                   min(round(self.window.height()*factor/self.factor),max_height))
                frame = self.window.frameGeometry()
                self.window.move(max(available.left(), min(frame.x(),available.right()-frame.width()+1)),
                                 max(available.top(), min(frame.y(),available.bottom()-frame.height()+1)))
            self.factor = factor
        finally:
            self.window.setUpdatesEnabled(True)
