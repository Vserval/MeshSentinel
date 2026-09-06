"""Review exact component edits before applying them to the Maya scene."""
try:
    from PySide6 import QtCore,QtWidgets
except ImportError:
    from PySide2 import QtCore,QtWidgets
from .model import Finding,RULE_MAP
from .repair import TITLES,actions_for,checked_mesh,plan_edit,apply_edit

class RepairDialog(QtWidgets.QDialog):
    def __init__(self,owner,finding):
        super().__init__(owner)
        self.owner,self.finding=owner,finding
        self.results=tuple(owner.report.meshes)
        self.settings=owner.report.settings
        self.mesh=checked_mesh(finding,self.results)
        self.plan=None
        self.setWindowTitle('Mesh Sentinel — 個別に修復 / 削除')
        self.setObjectName('sentinel')
        self.resize(int(660*owner.scale_combo.currentData()/100),690)
        layout=QtWidgets.QVBoxLayout(self)
        title=QtWidgets.QLabel(RULE_MAP[finding.rule].title)
        title.setStyleSheet('font-size:20px;font-weight:600;color:#83e2c5')
        layout.addWidget(title)
        path=QtWidgets.QLabel(finding.mesh); path.setWordWrap(True)
        path.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse); layout.addWidget(path)
        layout.addWidget(QtWidgets.QLabel('対象を選択（Ctrl / Shiftで複数選択）'))
        self.items=QtWidgets.QListWidget()
        self.items.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        for i in finding.ids:
            item=QtWidgets.QListWidgetItem('{}.{}[{}]'.format(finding.mesh.split('|')[-1],finding.component,i))
            item.setData(QtCore.Qt.UserRole,i); self.items.addItem(item)
        layout.addWidget(self.items,1)
        row=QtWidgets.QHBoxLayout()
        all_button=QtWidgets.QPushButton('全件を選択'); all_button.clicked.connect(self.items.selectAll); row.addWidget(all_button)
        preview=QtWidgets.QPushButton('対象を選択してフォーカス'); preview.clicked.connect(self.focus); row.addWidget(preview)
        layout.addLayout(row)
        self.operation=QtWidgets.QComboBox()
        for action in actions_for(finding): self.operation.addItem(TITLES[action],action)
        layout.addWidget(self.operation)
        self.description=QtWidgets.QTextBrowser(); self.description.setMinimumHeight(160)
        layout.addWidget(self.description)
        self.message=QtWidgets.QLabel('実行後に再検査します。MayaのUndo 1回で操作を戻せます。')
        self.message.setWordWrap(True); layout.addWidget(self.message)
        buttons=QtWidgets.QHBoxLayout(); buttons.addStretch()
        cancel=QtWidgets.QPushButton('閉じる'); cancel.clicked.connect(self.reject); buttons.addWidget(cancel)
        self.apply_button=QtWidgets.QPushButton('この内容を適用'); self.apply_button.setObjectName('primary')
        self.apply_button.clicked.connect(self.execute); buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)
        self.items.itemSelectionChanged.connect(self.update_plan)
        self.operation.currentIndexChanged.connect(self.update_plan)
        # Start with one component, especially for duplicate/intersecting faces:
        # choosing the row must not default to deleting every candidate.
        if self.items.count(): self.items.item(0).setSelected(True)

    def ids(self):
        return [item.data(QtCore.Qt.UserRole) for item in self.items.selectedItems()]

    def update_plan(self,*args):
        try:
            self.plan=plan_edit(self.finding,self.ids(),self.operation.currentData(),self.mesh,self.settings)
            components=self.plan.components
            self.description.setPlainText(TITLES[self.plan.action]+'\n\n'+self.plan.explanation+
                '\n\n適用対象: {:,} 箇所\n'.format(len(components))+'\n'.join(components[:100])+
                ('\n… 残り {:,} 箇所'.format(len(components)-100) if len(components)>100 else ''))
            self.apply_button.setEnabled(True)
        except (ValueError,RuntimeError) as exc:
            self.plan=None; self.description.setPlainText(str(exc)); self.apply_button.setEnabled(False)

    def focus(self):
        if not self.plan: return
        try:
            from .maya_bridge import select_findings
            # Show the actual expanded scope (hole loop / connected faces).
            f=Finding(self.finding.rule,self.plan.path,self.plan.kind,list(self.plan.ids),'適用範囲')
            select_findings([f],self.results,True)
        except Exception as exc: self.message.setText(str(exc))

    def execute(self):
        if self.plan is None: return
        try:
            self.owner._before_edit()
            path=apply_edit(self.plan)
        except Exception as exc:
            if self.owner.live.once: self.owner.live.reset()
            self.message.setText('適用できません: '+str(exc)); return
        self.owner._after_edit(path)
        self.accept()
