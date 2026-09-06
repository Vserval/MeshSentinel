"""PySide6 / PySide2 inspector. Host reads stay on the GUI thread."""
import datetime
import html
import os
import threading

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from shiboken6 import wrapInstance
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    from shiboken2 import wrapInstance

from .model import RULES, RULE_MAP, Settings, Report
from .engine import Inspector, Cancelled
from .exporter import export_report
from . import __version__


STYLE = """
QWidget { color:#dce5f1; font-family:'Segoe UI','Yu Gothic UI',sans-serif; font-size:12px; }
QDialog#sentinel { background:#10151d; }
QWidget#sentinelCanvas { background:#10151d; }
QFrame#panel, QFrame#metric { background:#171e29; border:1px solid #293342; border-radius:10px; }
QLabel#eyebrow { color:#76ddc0; font-size:10px; font-weight:700; letter-spacing:2px; }
QLabel#title { font-size:27px; font-weight:700; color:#f1f5fb; }
QLabel#muted { color:#8292a9; }
QLabel#metricValue { font-size:30px; font-weight:600; }
QLabel#badge { background:#21382f; color:#86e1be; border-radius:10px; padding:5px 12px; }
QPushButton { background:#222d3c; border:1px solid #354257; border-radius:6px; padding:9px 15px; font-weight:600; }
QPushButton:hover { background:#2d3d51; border-color:#58708d; }
QPushButton:pressed { background:#182431; }
QPushButton:disabled { color:#526178; background:#19212d; border-color:#273140; }
QPushButton#primary { background:#75dfbf; color:#0b2822; border-color:#75dfbf; }
QPushButton#primary:hover { background:#99edcf; }
QPushButton#primary:disabled { background:#254d43; color:#72998e; border-color:#254d43; }
QPushButton#quiet { background:transparent; border:0; padding:4px; color:#8cabc4; }
QPushButton#quiet:hover { color:#b9f5df; }
QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox { background:#111823; border:1px solid #303d50; border-radius:6px; padding:8px; selection-background-color:#35765f; }
QLineEdit:focus,QComboBox:focus,QDoubleSpinBox:focus,QSpinBox:focus { border-color:#71c9b0; }
QComboBox::drop-down { border:0; width:22px; }
QComboBox QAbstractItemView { background:#1d2836; selection-background-color:#34475b; }
QTreeWidget,QListWidget { background:#171e29; alternate-background-color:#1a2330; border:0; outline:0; }
QListWidget::item { padding:7px; }
QListWidget::item:selected { background:#294c49; color:#effff9; }
QTreeWidget::item { padding:9px 5px; border-bottom:1px solid #242f3f; }
QTreeWidget::item:hover { background:#253548; }
QTreeWidget::item:selected { background:#294c49; color:#effff9; }
QHeaderView::section { background:#1c2634; color:#8fa1b9; border:0; border-bottom:1px solid #344155; padding:11px 8px; font-size:11px; }
QCheckBox { spacing:9px; padding:5px 1px; }
QCheckBox::indicator { width:15px; height:15px; border:1px solid #536175; border-radius:4px; background:#141d28; }
QCheckBox::indicator:checked { background:#76ddc0; border-color:#a1f0d7; image:none; }
QCheckBox::indicator:disabled { background:#34483f; border-color:#43554f; }
QTabWidget::pane { border:0; background:transparent; }
QTabBar::tab { padding:10px 14px; color:#8091a8; border-bottom:2px solid transparent; }
QTabBar::tab:selected { color:#83e2c5; border-bottom:2px solid #83e2c5; }
QScrollArea { border:0; background:transparent; }
QScrollBar:vertical { width:7px; background:transparent; margin:0; }
QScrollBar::handle:vertical { background:#3b495d; border-radius:3px; min-height:25px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical { background:transparent; }
QTextBrowser { border:0; background:transparent; color:#a9b9cd; }
QProgressBar { border:0; border-radius:3px; background:#253142; max-height:5px; color:transparent; }
QProgressBar::chunk { background:#76ddc0; border-radius:3px; }
QSplitter::handle { background:transparent; width:12px; }
QMenu { background:#1c2735; border:1px solid #3c4d63; padding:5px; }
QMenu::item { padding:9px 22px; }
QMenu::item:selected { background:#30483f; }
QToolTip { background:#283648; color:#edf4fc; border:1px solid #4f647e; padding:7px; }
"""


class Mark(QtWidgets.QWidget):
    def __init__(self, parent=None, large=False):
        super().__init__(parent)
        self.setFixedSize(100 if large else 48, 100 if large else 48)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.scale(self.width()/48, self.height()/48)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#233d38"))
        painter.drawRoundedRect(QtCore.QRectF(0, 0, 48, 48), 11, 11)
        painter.setPen(QtGui.QPen(QtGui.QColor("#8ee9cc"), 1.6))
        polygon = QtGui.QPolygonF([QtCore.QPointF(*p) for p in ((24,8),(38,16),(38,32),(24,40),(10,32),(10,16))])
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawPolygon(polygon)
        for a,b in (((10,16),(24,24)),((38,16),(24,24)),((24,24),(24,40)),((24,8),(24,24))):
            painter.drawLine(QtCore.QPointF(*a), QtCore.QPointF(*b))
        painter.end()


class ScanWorker(QtCore.QThread):
    ready = QtCore.Signal(object)
    progress = QtCore.Signal(int, str)

    def __init__(self, meshes, settings, scope, errors, parent=None):
        super().__init__(parent)
        self.meshes, self.settings, self.scope, self.errors = meshes, settings, scope, errors
        self.stop = threading.Event()

    def run(self):
        report = Report(datetime.datetime.now(datetime.timezone.utc).isoformat(), self.settings,
                        errors=list(self.errors), scope=self.scope)
        try:
            for index, mesh in enumerate(self.meshes):
                if self.stop.is_set():
                    break
                def progress(i, total, title):
                    percent = 15 + int(85 * (index + i/total) / max(1, len(self.meshes)))
                    self.progress.emit(percent, "{} / {}  ·  {}  ·  {}".format(index+1, len(self.meshes), mesh.path.split("|")[-1], title))
                result = Inspector(self.settings, self.stop.is_set, progress).inspect(mesh)
                report.meshes.append(result)
        except Cancelled:
            self.stop.set()
        except Exception as exc:
            report.errors.append("{}: {}".format(type(exc).__name__, exc))
        report.cancelled = self.stop.is_set()
        self.ready.emit(report)


def label(text, name=None):
    widget = QtWidgets.QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


class InspectorWindow(QtWidgets.QDialog):
    def __init__(self, parent=None, settings_store=None):
        super().__init__(parent)
        self.setObjectName("sentinel")
        self.setWindowTitle("Mesh Sentinel  |  メッシュ検査")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowMinMaxButtonsHint)
        self.setMinimumSize(920, 720)
        self.resize(1160, 850)
        check_icon = os.path.join(os.path.dirname(__file__), "assets", "check.svg").replace("\\", "/")
        self._base_style = STYLE + '\nQCheckBox::indicator:checked { image:url("' + check_icon + '"); }'
        self.setStyleSheet(self._base_style)
        self.store = settings_store if settings_store is not None else QtCore.QSettings("MeshSentinel", "Inspector")
        self.worker = None
        self.report = None
        self.extracting = False
        self.abort_extraction = False
        self._build()
        from .ui_scaling import UiScaler
        self.scaler = UiScaler(self, self.canvas, self._base_style)
        self.overlay_timer = QtCore.QTimer(self)
        self.overlay_timer.setSingleShot(True)
        self.overlay_timer.setInterval(180)
        self.overlay_timer.timeout.connect(self._update_overlay)
        self._restore()
        from .hover_ui import HoverController
        self.hover = HoverController(self)
        from .live_review import LiveReview
        self.live = LiveReview(self)

    def _build(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0,0,0,0)
        self.canvas_scroll = QtWidgets.QScrollArea()
        self.canvas_scroll.setWidgetResizable(True)
        self.canvas_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.canvas = QtWidgets.QWidget()
        self.canvas.setObjectName("sentinelCanvas")
        self.canvas.setMinimumSize(920,780)
        self.canvas_scroll.setWidget(self.canvas)
        outer.addWidget(self.canvas_scroll)
        layout = QtWidgets.QVBoxLayout(self.canvas)
        layout.setContentsMargins(26, 24, 26, 18)
        layout.setSpacing(18)
        header = QtWidgets.QHBoxLayout()
        header.addWidget(Mark())
        brand = QtWidgets.QVBoxLayout()
        brand.setSpacing(1)
        brand.addWidget(label("GEOMETRY QUALITY TOOLKIT", "eyebrow"))
        brand.addWidget(label("Mesh Sentinel", "title"))
        header.addLayout(brand)
        header.addStretch()
        header.addWidget(label("UI倍率", "muted"))
        self.scale_combo = QtWidgets.QComboBox()
        self.scale_combo.setToolTip("文字・ボタン・余白を拡大します。設定は次回起動時も保持します。")
        for percent in (100,125,150,175,200):
            self.scale_combo.addItem("{}%".format(percent), percent)
        self.scale_combo.currentIndexChanged.connect(self._scale_changed)
        header.addWidget(self.scale_combo)
        header.addSpacing(8)
        self.badge = label("READY", "badge")
        header.addWidget(self.badge)
        header.addSpacing(8)
        self.menu_btn = QtWidgets.QPushButton("•••")
        self.menu_btn.setFixedWidth(42)
        menu = QtWidgets.QMenu(self)
        menu.addAction("シェルフに登録", self._shelf)
        menu.addAction("使い方・検出範囲", self._help)
        self.menu_btn.setMenu(menu)
        header.addWidget(self.menu_btn)
        layout.addLayout(header)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(label("検査対象", "muted"))
        self.scope = QtWidgets.QComboBox()
        self.scope.addItem("選択したメッシュ / 階層", "selected")
        self.scope.addItem("シーン内の全メッシュ", "scene")
        self.scope.setMinimumWidth(240)
        controls.addWidget(self.scope)
        controls.addStretch()
        self.cancel_btn = QtWidgets.QPushButton("キャンセル")
        self.cancel_btn.clicked.connect(self.cancel)
        self.cancel_btn.setEnabled(False)
        controls.addWidget(self.cancel_btn)
        self.scan_btn = QtWidgets.QPushButton("▶   メッシュを検査")
        self.scan_btn.setObjectName("primary")
        self.scan_btn.setMinimumWidth(190)
        self.scan_btn.setToolTip("選択またはシーン内のメッシュを非破壊で検査します")
        self.scan_btn.clicked.connect(self.scan)
        controls.addWidget(self.scan_btn)
        layout.addLayout(controls)

        metrics = QtWidgets.QHBoxLayout()
        metrics.setSpacing(12)
        self.metric_labels = {}
        for key, title, color, note in (("meshes", "検査メッシュ", "#eef4ff", "SCAN TARGETS"),
                                      ("error", "エラー", "#ff929c", "ERROR GROUPS"),
                                      ("warning", "警告", "#ecc17b", "WARNING GROUPS"),
                                      ("review", "要確認", "#9dbbff", "REVIEW GROUPS")):
            panel = QtWidgets.QFrame()
            panel.setObjectName("metric")
            box = QtWidgets.QVBoxLayout(panel)
            box.setContentsMargins(18, 12, 18, 12)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(label(title, "muted"))
            row.addStretch()
            dot_label = label("●")
            dot_label.setStyleSheet("color:{};font-size:9px".format(color))
            row.addWidget(dot_label)
            box.addLayout(row)
            value = label("—", "metricValue")
            value.setStyleSheet("color:" + color)
            self.metric_labels[key] = value
            box.addWidget(value)
            micro = label(note, "muted")
            micro.setStyleSheet("font-size:9px;letter-spacing:1px;color:#64768e")
            box.addWidget(micro)
            metrics.addWidget(panel)
        layout.addLayout(metrics)

        splitter = QtWidgets.QSplitter()
        left = QtWidgets.QFrame()
        left.setObjectName("panel")
        left.setMinimumWidth(245)
        left.setMaximumWidth(340)
        left_box = QtWidgets.QVBoxLayout(left)
        left_box.setContentsMargins(14, 10, 14, 12)
        self.tabs = QtWidgets.QTabWidget()
        left_box.addWidget(self.tabs)
        checks_page = QtWidgets.QWidget()
        check_box = QtWidgets.QVBoxLayout(checks_page)
        check_box.setContentsMargins(1, 9, 1, 0)
        shortcut = QtWidgets.QHBoxLayout()
        for title, mode in (("すべて", "all"), ("基本", "basic"), ("解除", "none")):
            b = QtWidgets.QPushButton(title)
            b.setObjectName("quiet")
            b.clicked.connect(lambda checked=False, value=mode: self._preset(value))
            shortcut.addWidget(b)
        check_box.addLayout(shortcut)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        inner.setStyleSheet("background:transparent")
        items = QtWidgets.QVBoxLayout(inner)
        items.setContentsMargins(0, 4, 0, 0)
        items.setSpacing(1)
        self.checks = {}
        for rule in RULES:
            checkbox = QtWidgets.QCheckBox(rule.title)
            checkbox.setToolTip(rule.description)
            checkbox.setChecked(True)
            self.checks[rule.key] = checkbox
            items.addWidget(checkbox)
        items.addStretch()
        scroll.setWidget(inner)
        check_box.addWidget(scroll)
        caption = label("13 CHECKS  /  NON-DESTRUCTIVE", "muted")
        caption.setStyleSheet("font-size:9px;color:#688098")
        check_box.addWidget(caption)
        self.tabs.addTab(checks_page, "検査項目")

        settings_page = QtWidgets.QWidget()
        settings_box = QtWidgets.QVBoxLayout(settings_page)
        settings_box.setContentsMargins(3, 16, 3, 0)
        self.distance = self._spin(settings_box, "距離許容誤差 / cm", 10, 1e-9, 1000, .00001)
        self.area = self._spin(settings_box, "面積閾値 / cm²", 14, 1e-14, 1000000, 1e-10)
        self.aspect = self._spin(settings_box, "細長さ / 最長辺 ÷ 最小高度", 1, 1., 1000000., 100.)
        settings_box.addWidget(label("比較上限 / 項目・メッシュごと", "muted"))
        self.budget = QtWidgets.QSpinBox()
        self.budget.setRange(1000, 100000000)
        self.budget.setSingleStep(100000)
        self.budget.setValue(2000000)
        self.budget.setGroupSeparatorShown(True)
        settings_box.addWidget(self.budget)
        info = label("距離はワールド座標のcmです。\nシーンの表示単位に依存しません。\n\n内部面・自己交差は高負荷です。\n上限到達は「未完了」と表示します。", "muted")
        info.setWordWrap(True)
        settings_box.addWidget(info)
        reset = QtWidgets.QPushButton("標準値に戻す")
        reset.clicked.connect(self._defaults)
        settings_box.addWidget(reset)
        settings_box.addStretch()
        self.tabs.addTab(settings_page, "しきい値")
        splitter.addWidget(left)

        right = QtWidgets.QFrame()
        right.setObjectName("panel")
        right_box = QtWidgets.QVBoxLayout(right)
        right_box.setContentsMargins(16, 16, 16, 14)
        right_box.setSpacing(12)
        toolbar = QtWidgets.QHBoxLayout()
        results_label = label("検査結果")
        results_label.setStyleSheet("font-weight:700;font-size:14px")
        toolbar.addWidget(results_label)
        toolbar.addStretch()
        self.export_btn = QtWidgets.QPushButton("レポート出力 ↗")
        self.export_btn.setEnabled(False)
        export_menu = QtWidgets.QMenu(self)
        for extension in ("HTML", "JSON", "CSV"):
            export_menu.addAction(extension, lambda ext=extension: self._export(ext))
        self.export_btn.setMenu(export_menu)
        toolbar.addWidget(self.export_btn)
        right_box.addLayout(toolbar)
        filters = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("メッシュ名・検査項目を検索…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        filters.addWidget(self.search)
        self.severity = QtWidgets.QComboBox()
        for title, value in (("すべての重要度", ""), ("エラー", "error"), ("警告", "warning"), ("要確認", "review")):
            self.severity.addItem(title, value)
        self.severity.currentIndexChanged.connect(self._filter)
        filters.addWidget(self.severity)
        right_box.addLayout(filters)

        overlay_controls = QtWidgets.QHBoxLayout()
        overlay_controls.addWidget(label("ビューポート", "muted"))
        self.overlay_mode = QtWidgets.QComboBox()
        self.overlay_mode.addItem("表示中の全問題", "all")
        self.overlay_mode.addItem("選択した結果のみ", "selected")
        self.overlay_mode.addItem("表示しない", "off")
        self.overlay_mode.currentIndexChanged.connect(self._schedule_overlay)
        overlay_controls.addWidget(self.overlay_mode, 1)
        self.overlay_xray = QtWidgets.QCheckBox("隠れた箇所も表示")
        self.overlay_xray.setChecked(False)
        self.overlay_xray.setToolTip("通常は裏面と遮蔽された問題を隠します。オンにすると内部・裏側の問題も透かして表示します。")
        self.overlay_xray.toggled.connect(self._schedule_overlay)
        overlay_controls.addWidget(self.overlay_xray)
        self.overlay_labels = QtWidgets.QCheckBox("ID")
        self.overlay_labels.setToolTip("コンポーネントIDを最大60個表示します")
        self.overlay_labels.toggled.connect(self._schedule_overlay)
        overlay_controls.addWidget(self.overlay_labels)
        self.overlay_clear_btn = QtWidgets.QPushButton("表示解除")
        self.overlay_clear_btn.clicked.connect(lambda: self.overlay_mode.setCurrentIndex(2))
        overlay_controls.addWidget(self.overlay_clear_btn)
        right_box.addLayout(overlay_controls)
        hover_controls = QtWidgets.QHBoxLayout()
        self.overlay_hover = QtWidgets.QCheckBox("マウスで詳細")
        self.overlay_hover.setToolTip("問題箇所でマウスを少し止めると説明を表示。手前の面だけを判定します。")
        self.overlay_hover.toggled.connect(lambda: self.hover.hide() if hasattr(self,'hover') else None)
        hover_controls.addWidget(self.overlay_hover)
        self.auto_review = QtWidgets.QCheckBox('修正を自動確認')
        self.auto_review.setToolTip('編集を止めて約0.7秒後に、変更メッシュだけ再検査。解消した問題を一覧と表示から外します。')
        self.auto_review.toggled.connect(self._auto_changed)
        hover_controls.addWidget(self.auto_review)
        hover_controls.addWidget(label("問題箇所にカーソルを重ねると説明を表示", "muted"),1)
        right_box.addLayout(hover_controls)
        self.overlay_status = label("赤: エラー  /  黄: 警告  /  青: 要確認  ·  検査後に問題箇所を表示", "muted")
        self.overlay_status.setWordWrap(True)
        right_box.addWidget(self.overlay_status)

        self.stack = QtWidgets.QStackedWidget()
        empty = QtWidgets.QWidget()
        empty_box = QtWidgets.QVBoxLayout(empty)
        empty_box.addStretch()
        empty_box.addWidget(Mark(large=True), 0, QtCore.Qt.AlignHCenter)
        empty_box.addSpacing(16)
        self.empty_title = label("形状の品質を、ひと目で。")
        self.empty_title.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_title.setStyleSheet("font-size:20px;font-weight:600")
        empty_box.addWidget(self.empty_title)
        self.empty_text = label("メッシュを選択し、検査を開始してください。\n問題箇所の特定から個別修復まで、この画面で。", "muted")
        self.empty_text.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_text.setWordWrap(True)
        empty_box.addWidget(self.empty_text)
        empty_box.addStretch()
        self.stack.addWidget(empty)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(("検査項目 / メッシュ", "重要度", "対象", "件数", "状態"))
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(16)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column, width in ((1, 67), (2, 46), (3, 50), (4, 70)):
            self.tree.header().setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
            self.tree.setColumnWidth(column, width)
        self.tree.itemSelectionChanged.connect(self._details)
        self.tree.itemDoubleClicked.connect(lambda *args: self._select(True))
        self.stack.addWidget(self.tree)
        right_box.addWidget(self.stack, 1)
        self.detail = QtWidgets.QTextBrowser()
        self.detail.setMinimumHeight(70)
        self.detail.setMaximumHeight(92)
        self.detail.setHtml("<span style='color:#7389a3'>INSPECTION NOTES</span><br>結果を選択すると、検出理由とコンポーネントIDを表示します。")
        right_box.addWidget(self.detail)
        repair_row=QtWidgets.QHBoxLayout()
        repair_hint=label('結果行を1つ選び、コンポーネント単位で編集', 'muted')
        repair_row.addWidget(repair_hint,1)
        self.repair_btn=QtWidgets.QPushButton('個別に修復 / 削除…')
        self.repair_btn.setEnabled(False)
        self.repair_btn.clicked.connect(self._repair)
        repair_row.addWidget(self.repair_btn)
        self.repair_all_btn=QtWidgets.QPushButton('すべての問題を修復…')
        self.repair_all_btn.setEnabled(False)
        self.repair_all_btn.clicked.connect(self._repair_all)
        repair_row.addWidget(self.repair_all_btn)
        right_box.addLayout(repair_row)
        uv_row=QtWidgets.QHBoxLayout()
        uv_row.addWidget(label('平面投影後のUnfoldエラーに対応', 'muted'),1)
        self.uv_repair_btn=QtWidgets.QPushButton('UV展開エラーを修復（三角化なし）…')
        self.uv_repair_btn.clicked.connect(self._repair_uv)
        uv_row.addWidget(self.uv_repair_btn)
        right_box.addLayout(uv_row)
        actions = QtWidgets.QHBoxLayout()
        self.row_count = label("検査待ち", "muted")
        actions.addWidget(self.row_count)
        actions.addStretch()
        self.select_btn = QtWidgets.QPushButton("問題箇所を選択")
        self.select_btn.clicked.connect(lambda: self._select(False))
        self.select_btn.setEnabled(False)
        self.select_btn.setToolTip('結果一覧の問題行を選択してください。Ctrl / Shiftで複数行を選べます。')
        actions.addWidget(self.select_btn)
        self.frame_btn = QtWidgets.QPushButton("選択してフォーカス")
        self.frame_btn.clicked.connect(lambda: self._select(True))
        self.frame_btn.setEnabled(False)
        actions.addWidget(self.frame_btn)
        right_box.addLayout(actions)
        splitter.addWidget(right)
        splitter.setSizes((255, 800))
        layout.addWidget(splitter, 1)
        footer = QtWidgets.QVBoxLayout()
        footer.setSpacing(8)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        footer.addWidget(self.progress_bar)
        status_row = QtWidgets.QHBoxLayout()
        self.status = label("●  準備完了  ·  メッシュを変更せずに検査します", "muted")
        status_row.addWidget(self.status, 1)
        status_row.addWidget(label("API 2.0  /  v" + __version__, "muted"))
        footer.addLayout(status_row)
        layout.addLayout(footer)

    def _spin(self, layout, title, decimals, minimum, maximum, value):
        layout.addWidget(label(title, "muted"))
        spin = QtWidgets.QDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSingleStep(value)
        layout.addWidget(spin)
        layout.addSpacing(8)
        return spin

    def _defaults(self):
        s = Settings()
        for name in ("distance", "area", "aspect"):
            getattr(self, name).setValue(getattr(s, name))
        self.budget.setValue(s.pair_budget)

    def _preset(self, mode):
        for key, widget in self.checks.items():
            widget.setChecked(mode == "all" or (mode == "basic" and key not in ("internal", "intersection", "t_junction")))

    def _settings(self):
        return Settings(self.distance.value(), self.area.value(), self.aspect.value(), self.budget.value(),
                        tuple(key for key, widget in self.checks.items() if widget.isChecked()))

    def _restore(self):
        for name in ("distance", "area", "aspect"):
            try:
                getattr(self, name).setValue(float(self.store.value(name, getattr(Settings(), name))))
            except (ValueError, TypeError):
                pass
        try:
            self.budget.setValue(int(self.store.value("pair_budget", 2000000)))
        except (ValueError, TypeError):
            pass
        for key, widget in self.checks.items():
            widget.setChecked(str(self.store.value("checks/" + key, "true")).lower() == "true")
        geometry = self.store.value("geometry")
        if isinstance(geometry, QtCore.QByteArray):
            self.restoreGeometry(geometry)
        try:
            percent = int(self.store.value("ui_scale",125))
        except (ValueError,TypeError):
            percent = 125
        index = self.scale_combo.findData(percent)
        self.scale_combo.blockSignals(True)
        self.scale_combo.setCurrentIndex(index if index >= 0 else 1)
        self.scale_combo.blockSignals(False)
        self.scaler.apply(self.scale_combo.currentData(), resize=geometry is None)
        mode = self.overlay_mode.findData(self.store.value("overlay_mode","all"))
        self.overlay_mode.setCurrentIndex(mode if mode >= 0 else 0)
        # New key intentionally migrates the former always-on default to a
        # readable, depth-tested view, while retaining choices made in this UI.
        self.overlay_xray.setChecked(str(self.store.value("overlay_show_hidden_v2","false")).lower() == "true")
        self.overlay_labels.setChecked(str(self.store.value("overlay_labels","false")).lower() == "true")
        self.overlay_hover.setChecked(str(self.store.value("overlay_hover","true")).lower() == "true")
        self.auto_review.setChecked(str(self.store.value('auto_review','true')).lower()=='true')

    def _auto_changed(self,*args):
        if not hasattr(self,'live'):
            return
        if self.auto_review.isChecked() and self.report:
            self.live.bind(self.report)
            self.live.request_all()
        else:
            self.live.reset()
        self._schedule_overlay()

    def _scale_changed(self, *args):
        if hasattr(self,"scaler"):
            self.scaler.apply(self.scale_combo.currentData())
            self.store.setValue("ui_scale",self.scale_combo.currentData())

    def _schedule_overlay(self, *args):
        if not hasattr(self,"overlay_timer"):
            return
        if self.overlay_mode.currentData() == "off":
            self._clear_overlay()
            self.overlay_status.setText("ビューポート表示を解除しました")
        elif self.report and not self.extracting and self.worker is None:
            self.overlay_timer.start()

    def _clear_overlay(self):
        if hasattr(self,'hover'):
            self.hover.hide()
        if hasattr(self,"overlay_timer"):
            self.overlay_timer.stop()
        from . import viewport
        if viewport.SESSION.handle is not None or viewport.SESSION.callbacks:
            viewport.SESSION.clear()

    def _overlay_notice(self, text):
        self.overlay_status.setText(text)

    def _update_overlay(self):
        if self.extracting or self.worker is not None or not self.report or not self.isVisible():
            return
        mode = self.overlay_mode.currentData()
        if mode == "off":
            self._clear_overlay()
            return
        from .model import Finding
        findings = self._selected() if mode == "selected" else []
        if mode == "all":
            for i in range(self.tree.topLevelItemCount()):
                parent = self.tree.topLevelItem(i)
                if parent.isHidden():
                    continue
                for j in range(parent.childCount()):
                    item = parent.child(j)
                    data = item.data(0,QtCore.Qt.UserRole)
                    if not item.isHidden() and isinstance(data,Finding):
                        findings.append(data)
        try:
            from .viewport import SESSION
            if hasattr(self,'live'):
                findings=[f for f in findings if f.mesh not in self.live.stale]
            refresh=self.live.changed if hasattr(self,'live') and (self.auto_review.isChecked() or self.live.once) and self.live.targets else None
            payload = SESSION.show(findings,self.report.meshes,self.overlay_xray.isChecked(),
                                   self.overlay_labels.isChecked(),self._overlay_notice,refresh)
            self.overlay_status.setText("ビューポート: {:,} / {:,} 箇所  ·  赤: エラー / 黄: 警告 / 青: 要確認{}".format(
                payload["displayed"],payload["total"],"  ·  表示上限あり。結果を絞り込んでください" if payload["truncated"] else ""))
        except Exception as exc:
            self._clear_overlay()
            self.overlay_status.setText("表示できません: " + str(exc))

    def _save(self):
        for name in ("distance", "area", "aspect"):
            self.store.setValue(name, getattr(self, name).value())
        self.store.setValue("pair_budget", self.budget.value())
        for key, widget in self.checks.items():
            self.store.setValue("checks/" + key, "true" if widget.isChecked() else "false")
        self.store.setValue("geometry", self.saveGeometry())
        self.store.setValue("ui_scale", self.scale_combo.currentData())
        self.store.setValue("overlay_mode",self.overlay_mode.currentData())
        self.store.setValue("overlay_show_hidden_v2","true" if self.overlay_xray.isChecked() else "false")
        self.store.setValue("overlay_labels","true" if self.overlay_labels.isChecked() else "false")
        self.store.setValue("overlay_hover","true" if self.overlay_hover.isChecked() else "false")
        self.store.setValue('auto_review','true' if self.auto_review.isChecked() else 'false')

    def _busy(self, state):
        self.uv_repair_btn.setEnabled(not state)
        self.scan_btn.setEnabled(not state)
        self.scope.setEnabled(not state)
        self.tabs.setEnabled(not state)
        self.cancel_btn.setEnabled(state)
        self.export_btn.setEnabled(not state and self.report is not None)
        self.select_btn.setEnabled(False)
        self.frame_btn.setEnabled(False)
        self.badge.setText("SCANNING" if state else "READY")
        self.repair_btn.setEnabled(False)
        self.repair_all_btn.setEnabled(False)
        for widget in (self.overlay_mode,self.overlay_xray,self.overlay_labels,self.overlay_clear_btn,self.overlay_hover):
            widget.setEnabled(not state)
        if state:
            self._clear_overlay()

    def scan(self):
        if self.live.worker is not None:
            self.live.reset()
            QtCore.QTimer.singleShot(100,self.scan)
            return
        if self.extracting or self.worker is not None:
            return
        self.scan_settings = self._settings()
        if not self.scan_settings.enabled:
            self.status.setText("検査項目を1つ以上選択してください。")
            return
        try:
            from .maya_bridge import mesh_paths
            self.paths = mesh_paths(self.scope.currentData())
        except Exception as exc:
            self._error(exc)
            return
        if not self.paths:
            self.status.setText("対象のメッシュがありません。メッシュまたは親グループを選択してください。")
            return
        self.live.reset()
        self._save()
        self._busy(True)
        self.report = None
        self.tree.clear()
        for value in self.metric_labels.values():
            value.setText("—")
        self.metric_labels["meshes"].setText(str(len(self.paths)))
        self.stack.setCurrentIndex(0)
        self.empty_title.setText("メッシュを検査しています")
        self.empty_text.setText("トポロジーとジオメトリの問題を検出します。\nキャンセルした検査は未完了として記録します。")
        self.detail.setPlainText("スナップショット取得後の形状解析はバックグラウンドで実行します。")
        self.extracting, self.abort_extraction = True, False
        self.snapshots, self.extraction_errors = [], []
        self.extract_index = 0
        QtCore.QTimer.singleShot(0, self._extract_next)

    def _extract_next(self):
        if self.abort_extraction:
            self.extracting = False
            self._busy(False)
            report = Report(datetime.datetime.now(datetime.timezone.utc).isoformat(), self.scan_settings,
                            errors=self.extraction_errors, cancelled=True, scope=self.scope.currentData())
            self._received(report)
            return
        if self.extract_index >= len(self.paths):
            self.extracting = False
            self.worker = ScanWorker(self.snapshots, self.scan_settings, self.scope.currentData(), self.extraction_errors, self)
            self.worker.progress.connect(self._progress)
            self.worker.ready.connect(self._received)
            self.worker.finished.connect(self._finished)
            self.worker.start()
            return
        path = self.paths[self.extract_index]
        self._progress(int(15*self.extract_index/len(self.paths)), "スナップショット取得  ·  " + path.split("|")[-1])
        try:
            from .maya_bridge import snapshot
            self.snapshots.append(snapshot(path))
        except Exception as exc:
            self.extraction_errors.append("{}: {}".format(path, exc))
        self.extract_index += 1
        QtCore.QTimer.singleShot(0, self._extract_next)

    def cancel(self):
        self.abort_extraction = True
        if self.worker:
            self.worker.stop.set()
        self.cancel_btn.setEnabled(False)
        self.status.setText("キャンセルしています…")

    def _progress(self, value, message):
        self.progress_bar.setValue(value)
        self.status.setText(message)

    def _finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.snapshots = []
        self._busy(False)
        if self.report:
            self.badge.setText("COMPLETE" if self.report.complete else "INCOMPLETE")
            self.live.bind(self.report)
        self._details()
        self._schedule_overlay()

    def _received(self, report):
        self.report = report
        self.tree.clear()
        counts = {key: 0 for key in ("error", "warning", "review")}
        color = {"error": "#ff929c", "warning": "#ecc17b", "review": "#9dbbff"}
        severity_titles = {"error": "エラー", "warning": "警告", "review": "要確認"}
        self.tree.setUpdatesEnabled(False)
        for mesh in report.meshes:
            display_name = mesh.path.split("|")[-2] if "|" in mesh.path.strip("|") else mesh.path.split("|")[-1]
            parent = QtWidgets.QTreeWidgetItem([display_name, "", "", "", "検査済"])
            parent.setToolTip(0, mesh.path)
            parent.setData(0, QtCore.Qt.UserRole, mesh)
            self.tree.addTopLevelItem(parent)
            for finding in mesh.findings:
                counts[finding.severity] += 1
                item = QtWidgets.QTreeWidgetItem([RULE_MAP[finding.rule].title, severity_titles[finding.severity],
                                                  finding.component, format(len(finding.ids), ","), "検出"])
                item.setData(0, QtCore.Qt.UserRole, finding)
                item.setToolTip(0, finding.message)
                item.setForeground(1, QtGui.QBrush(QtGui.QColor(color[finding.severity])))
                parent.addChild(item)
            for key, state in mesh.checks.items():
                if state not in ("complete", "disabled"):
                    item = QtWidgets.QTreeWidgetItem([RULE_MAP[key].title, "要確認", "—", "—", "未完了"])
                    item.setToolTip(0, state)
                    parent.addChild(item)
                    parent.setText(4, "未完了")
            if parent.childCount() == 0:
                parent.addChild(QtWidgets.QTreeWidgetItem(["有効な検査項目で検出なし", "", "", "0", "完了"]))
            parent.setExpanded(True)
        for error in report.errors:
            item = QtWidgets.QTreeWidgetItem(["読み取り / 実行エラー", "エラー", "—", "—", "失敗"])
            item.setToolTip(0, error)
            self.tree.addTopLevelItem(item)
        self.tree.setUpdatesEnabled(True)
        for key, value in counts.items():
            self.metric_labels[key].setText(str(value))
        self.metric_labels["meshes"].setText(str(len(report.meshes)))
        self.stack.setCurrentIndex(1)
        self.progress_bar.setValue(100 if report.complete else self.progress_bar.value())
        self.badge.setText("COMPLETE" if report.complete else "INCOMPLETE")
        total = sum(counts.values())
        self.status.setText("{}  ·  {} メッシュ / {} 検出グループ  ·  {:.2f} 秒（解析）".format(
            "検査完了" if report.complete else "未完了 — 実行状態とメモを確認", len(report.meshes), total,
            sum(m.seconds for m in report.meshes)))
        self.export_btn.setEnabled(True)
        notes = report.errors + [m.path.split("|")[-1] + " / " + note for m in report.meshes for note in m.notes]
        if report.cancelled:
            notes.insert(0, "キャンセルされたため、未検査のメッシュ・項目があります。")
        self.detail.setPlainText("\n".join(notes) if notes else "結果を選択して詳細を表示。上の件数は検出グループ数、表の件数はコンポーネント数です。")
        self._filter()

    def _filter(self, *args):
        if not self.report:
            return
        query, severity = self.search.text().casefold(), self.severity.currentData()
        visible = 0
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            shown = 0
            for j in range(parent.childCount()):
                item = parent.child(j)
                data = item.data(0, QtCore.Qt.UserRole)
                haystack = (parent.toolTip(0) + " " + item.text(0) + " " + item.toolTip(0)).casefold()
                matches = query in haystack and (not severity or getattr(data, "severity", "review") == severity)
                item.setHidden(not matches)
                if not matches:
                    item.setSelected(False)
                shown += int(matches)
                visible += int(matches)
            parent.setHidden(shown == 0 if parent.childCount() else query not in parent.toolTip(0).casefold())
            if parent.isHidden():
                parent.setSelected(False)
        self.row_count.setText("{} 行を表示".format(visible))
        self._schedule_overlay()

    def _selected(self):
        from .model import Finding
        findings, seen = [], set()
        for item in self.tree.selectedItems():
            items = [item] + [item.child(i) for i in range(item.childCount()) if not item.child(i).isHidden()]
            for child in items:
                data = child.data(0, QtCore.Qt.UserRole)
                if isinstance(data, Finding) and id(data) not in seen:
                    seen.add(id(data))
                    findings.append(data)
        return findings

    def _details(self):
        findings = self._selected()
        self.repair_all_btn.setEnabled(bool(self.report and self.report.complete and
            any(r.findings for r in self.report.meshes)) and not self.worker and not self.extracting
            and not self.live.worker and not self.live.stale)
        enabled = bool(findings) and not self.extracting and self.worker is None
        self.select_btn.setEnabled(enabled)
        self.frame_btn.setEnabled(enabled)
        self.repair_btn.setEnabled(enabled and len(findings)==1 and not self.live.worker
                                   and not {f.mesh for f in findings}.intersection(self.live.stale))
        if findings:
            text = []
            for f in findings[:4]:
                ids = ", ".join(map(str, f.ids[:40])) + (" …" if len(f.ids) > 40 else "")
                text.append("<b>{}</b> · {}<br>{}<br><span style='color:#86d9c0'>{}: {}</span>".format(
                    html.escape(RULE_MAP[f.rule].title), html.escape(f.mesh), html.escape(f.message), f.component, ids))
            self.detail.setHtml("<br><br>".join(text))
        elif self.tree.selectedItems():
            item = self.tree.selectedItems()[0]
            self.detail.setPlainText(item.toolTip(0) or item.text(0))
        if hasattr(self,"overlay_mode") and self.overlay_mode.currentData() == "selected":
            self._schedule_overlay()

    def _select(self, frame):
        findings = self._selected()
        if {f.mesh for f in findings}.intersection(self.live.stale):
            self.status.setText('変更箇所を自動確認しています。更新後に選択してください。')
            return
        if self.worker is not None or self.extracting or not self.report:
            return
        if not findings:
            self.status.setText('結果一覧で問題の行を選択してください。')
            return
        try:
            from .maya_bridge import select_findings
            select_findings(findings, self.report.meshes, frame)
            self.status.setText("問題箇所を選択しました。編集後に自動確認します。" if self.auto_review.isChecked()
                                else "問題箇所を選択しました。編集後は再検査してください。")
        except Exception as exc:
            self._error(exc)

    def _repair(self):
        findings=self._selected()
        if len(findings)!=1 or self.worker or self.extracting:
            self.status.setText('修復する問題の行を1つ選択してください。')
            return
        if self.live.worker or self.live.stale:
            self.status.setText('自動確認の完了後に修復を開いてください。')
            return
        try:
            from .repair_ui import RepairDialog
            dialog=RepairDialog(self,findings[0])
            dialog.exec() if hasattr(dialog,'exec') else dialog.exec_()
            dialog.deleteLater()
        except Exception as exc:
            self._error(exc)

    def _repair_uv(self):
        if self.worker or self.extracting or self.live.worker:
            self.status.setText('検査の完了後にUV修復を実行してください。'); return
        try:
            from .maya_bridge import mesh_paths
            from .uv_ui import UVRepairDialog
            dialog=UVRepairDialog(self,mesh_paths('selected'))
            dialog.exec() if hasattr(dialog,'exec') else dialog.exec_()
            dialog.deleteLater()
        except Exception as exc: self._error(exc)

    def _repair_all(self):
        if not self.report or self.worker or self.extracting or self.live.worker or self.live.stale:
            self.status.setText('検査・自動確認の完了後に一括修復してください。')
            return
        from .bulk_ui import BulkRepairDialog
        dialog=BulkRepairDialog(self)
        dialog.exec() if hasattr(dialog,'exec') else dialog.exec_()
        dialog.deleteLater()

    def _before_edit(self):
        if not self.auto_review.isChecked():
            self.live.bind(self.report,once=True)

    def _after_edit(self,path):
        self.hover.hide()
        # Edits explicitly made in this tool always refresh the report, even
        # when automatic review for external editing is switched off.
        if not self.live.targets:
            self.live.bind(self.report,once=not self.auto_review.isChecked())
        self.live.changed([path] if isinstance(path,str) else path)
        self.live.timer.start()
        self.status.setText('操作を適用しました。再検査して残る問題を確認しています。Undo 1回で戻せます。')

    def _export(self, extension):
        if not self.report:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "検査レポートを保存", "mesh_sentinel_report." + extension.lower(),
                                                       "{} (*.{})".format(extension, extension.lower()))
        if path:
            if not path.lower().endswith("." + extension.lower()):
                path += "." + extension.lower()
            try:
                export_report(self.report, path)
                self.status.setText("保存しました: " + path)
            except Exception as exc:
                self._error(exc)

    def _shelf(self):
        try:
            from .maya_bridge import install_shelf
            install_shelf()
            self.status.setText("現在のシェルフに Mesh Sentinel を登録しました。")
        except Exception as exc:
            self._error(exc)

    def _help(self):
        QtWidgets.QMessageBox.information(self, "Mesh Sentinel — 使い方",
            "1. メッシュまたは親グループを選択\n2. 項目としきい値を設定して検査\n3. 結果行を選び、問題箇所を選択\n4. 個別に修復 / 削除で対象と操作を選択\n5. 適用後に再検査 / レポート出力\n\n"
            "複数行はCtrl / Shiftで選択できます。ダブルクリックでフォーカス。\n"
            "距離はワールド座標cm。異なるメッシュ間の干渉は対象外。\n"
            "内部面は閉殻内の別の面を対象にした候補判定です。\n"
            "法線の正しい向き・意図した開口・内殻は人の確認が必要です。\n"
            "修復はプレビューした範囲だけに適用し、Undo 1回で戻せます。\n"
            "自己交差・非多様体・Tジャンクション等は対象面の削除を選べます。\n"
            "非常に大きな単一メッシュはスナップショット取得中にUIが待機します。\n\n"
            "v" + __version__ + " / 詳細は同梱README_JA.mdを参照。")

    def _error(self, exc):
        self.status.setText(str(exc))
        QtWidgets.QMessageBox.warning(self, "Mesh Sentinel", str(exc))

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self,'hover') and not self.hover.disposed:
            self.hover.timer.start()
        if hasattr(self,'live') and self.report:
            if not self.live.targets:
                self.live.bind(self.report)
            self.live.request_all()

    def hideEvent(self, event):
        if hasattr(self,'hover'):
            self.hover.timer.stop()
            self.hover.hide()
        super().hideEvent(event)

    def closeEvent(self, event):
        if self.live.worker is not None:
            self.live.reset()
            QtCore.QTimer.singleShot(100,self.close)
            event.ignore()
            return
        if self.worker is not None or self.extracting:
            self.cancel()
            self.status.setText("停止処理中です。終了後にウィンドウを閉じてください。")
            event.ignore()
            return
        self._save()
        self.live.reset()
        self._clear_overlay()
        event.accept()


_window = None


def show():
    global _window
    if not isinstance(QtWidgets.QApplication.instance(), QtWidgets.QApplication):
        raise RuntimeError("UIはMayaの通常起動内で使用してください。mayapyでは先にQApplicationが必要です。")
    if _window is None:
        import maya.OpenMayaUI as omui
        pointer = omui.MQtUtil.mainWindow()
        parent = wrapInstance(int(pointer), QtWidgets.QWidget) if pointer else None
        _window = InspectorWindow(parent)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
