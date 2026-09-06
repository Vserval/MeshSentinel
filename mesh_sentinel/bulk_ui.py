"""Whole-report repair preview and execution."""
try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide2 import QtWidgets
from .model import RULE_MAP
from .repair import TITLES
from .bulk_repair import BulkOptions,action_for,repair_all

class BulkRepairDialog(QtWidgets.QDialog):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner; self.report=owner.report
        self.setObjectName('sentinel'); self.setWindowTitle('Mesh Sentinel — すべての問題を修復')
        self.resize(900,700)
        layout=QtWidgets.QVBoxLayout(self)
        title=QtWidgets.QLabel('すべての問題を修復')
        title.setStyleSheet('font-size:22px;font-weight:600;color:#83e2c5'); layout.addWidget(title)
        note=QtWidgets.QLabel('最後の検査結果の全メッシュが対象です。検索・重要度フィルターで隠れている結果も含みます。\n'
            '操作ごとに再検出し、最後に再検査します。解消できない問題は残り件数に表示します。')
        note.setWordWrap(True); layout.addWidget(note)
        self.fill=QtWidgets.QCheckBox('穴埋めを含める（意図した開口も閉じます）'); self.fill.setChecked(True)
        self.normals=QtWidgets.QCheckBox('法線修正を含める（表裏・陰影を変更します）'); self.normals.setChecked(True)
        self.delete=QtWidgets.QCheckBox('修復に面削除が必要な問題も処理する（形状・接続面を削除します）')
        self.triangulate=QtWidgets.QCheckBox('三角化を許可する（N-gon・凹N-gon・穴付き面）')
        for check in (self.fill,self.normals,self.delete,self.triangulate):
            layout.addWidget(check); check.toggled.connect(self.preview)
        self.summary=QtWidgets.QTextBrowser(); layout.addWidget(self.summary,1)
        self.status=QtWidgets.QLabel('全体をUndo 1回で戻せます。全面削除になる操作は行いません。')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        buttons=QtWidgets.QHBoxLayout(); buttons.addStretch()
        self.close_button=QtWidgets.QPushButton('閉じる'); self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        self.apply_button=QtWidgets.QPushButton('この内容ですべて修復'); self.apply_button.setObjectName('primary')
        self.apply_button.clicked.connect(self.execute); buttons.addWidget(self.apply_button)
        layout.addLayout(buttons); self.preview()

    def options(self):
        return BulkOptions(self.fill.isChecked(),self.normals.isChecked(),self.delete.isChecked(),self.triangulate.isChecked())

    def preview(self,*args):
        if not hasattr(self,'summary'): return
        lines=[]; supported=False
        names=dict(TITLES,deduplicate='重複面を1枚残して整理',normals='法線を修正')
        for mesh in self.report.meshes:
            if not mesh.findings: continue
            lines.append(mesh.path)
            for finding in mesh.findings:
                action=action_for(finding.rule,self.options())
                if finding.rule=='boundary' and finding.component=='f' and action=='fill':
                    action='triangulate' if self.triangulate.isChecked() else None
                supported=supported or bool(action)
                lines.append('  {} / {} {:,} 箇所 → {}'.format(RULE_MAP[finding.rule].title,
                    finding.component,len(finding.ids),names.get(action,'個別確認として残す')))
            lines.append('')
        lines.append('重複面は完全一致する単純面を整理します。許容誤差だけで一致する候補は残します。\n'
                     'IDと件数は検査時の値です。編集後の対象は再検出して更新するため変わる場合があります。')
        self.summary.setPlainText('\n'.join(lines))
        self.apply_button.setEnabled(supported and self.report.complete)

    def progress(self,path,rule):
        self.status.setText(path+' / '+rule+' を確認中…')
        self.status.repaint()

    def execute(self):
        self.apply_button.setEnabled(False); self.close_button.setEnabled(False)
        for check in (self.fill,self.normals,self.delete,self.triangulate): check.setEnabled(False)
        self.owner._before_edit()
        try:
            # No processEvents inside an open Undo chunk: unrelated commands
            # must never become part of this batch's rollback or Undo.
            changed,log=repair_all(self.report,self.options(),self.progress)
        except Exception as exc:
            if self.owner.live.once: self.owner.live.reset()
            self.status.setText('適用できません: '+str(exc)+'\n途中の変更がある場合は一括操作を戻しました。')
            self.close_button.setEnabled(True)
            return
        if changed:
            self.owner._after_edit(changed)
        elif self.owner.live.once:
            self.owner.live.reset()
        self.summary.setPlainText('\n'.join(log) or '変更が必要な箇所はありませんでした。')
        self.status.setText('{} メッシュを変更しました。残る問題は再検査後のメイン画面で確認してください。'.format(len(changed)))
        self.close_button.setEnabled(True)
