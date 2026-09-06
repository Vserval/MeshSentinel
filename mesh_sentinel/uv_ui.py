"""Dedicated no-triangulation Unfold preparation UI."""
try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide2 import QtWidgets
from .uv_repair import inspect_uv,repair_uv

class UVRepairDialog(QtWidgets.QDialog):
    def __init__(self,owner,paths):
        super().__init__(owner); self.owner=owner
        self.states=[inspect_uv(path) for path in paths]
        if not self.states: raise RuntimeError('Mayaで修復するメッシュを選択してください。')
        self.setObjectName('sentinel'); self.setWindowTitle('UV展開エラーを修復 — 三角化なし')
        self.resize(880,660)
        layout=QtWidgets.QVBoxLayout(self)
        title=QtWidgets.QLabel('UV展開エラーを修復（三角化なし）')
        title.setStyleSheet('font-size:21px;font-weight:600;color:#83e2c5'); layout.addWidget(title)
        description=QtWidgets.QLabel('平面投影の後に実行してください。面数・四角形・各面の頂点位置を保ち、問題の接続とUVを分離します。\n'
            '接続部に切れ目ができ、頂点数・UV数が増える場合があります。三角化・面削除・頂点移動はしません。')
        description.setWordWrap(True); layout.addWidget(description)
        self.split=QtWidgets.QCheckBox('形状自体の非多様体も分離する（頂点接続に切れ目ができます）')
        self.split.setChecked(True); layout.addWidget(self.split)
        self.create=QtWidgets.QCheckBox('UV未割当の面だけに平面投影を作成する（既存UVの座標を保つ）')
        self.create.setChecked(True); layout.addWidget(self.create)
        self.unfold=QtWidgets.QCheckBox('修復後にUnfold3Dで展開する（現在のUVセットの座標が変わります）')
        self.unfold.setChecked(False); layout.addWidget(self.unfold)
        self.axis=QtWidgets.QComboBox()
        for axis in ('z','y','x'): self.axis.addItem('未割当UVの投影方向: '+axis.upper(),axis)
        layout.addWidget(self.axis)
        self.summary=QtWidgets.QTextBrowser(); layout.addWidget(self.summary,1)
        lines=[]
        for state in self.states:
            lines.append('{}\n  {} 面 / 四角形 {} 面 / {} 頂点\n  形状: 非多様体頂点 {} / エッジ {}\n'
                         '  UVセット {}: 非多様体UV {} / エッジ {} / 未割当面 {}\n'.format(
                state.path,len(state.face_sizes),state.face_sizes.count(4),state.vertices,
                len(state.nonmanifold_vertices),len(state.nonmanifold_edges),state.uv_set,
                len(state.nonmanifold_uvs),len(state.nonmanifold_uv_edges),len(state.missing_faces)))
        self.summary.setPlainText('\n'.join(lines))
        self.status=QtWidgets.QLabel('修復後にMayaのUnfold事前検証を行います。操作全体をUndo 1回で戻せます。')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        buttons=QtWidgets.QHBoxLayout(); buttons.addStretch()
        self.close_button=QtWidgets.QPushButton('閉じる'); self.close_button.clicked.connect(self.reject); buttons.addWidget(self.close_button)
        self.apply_button=QtWidgets.QPushButton('三角化せずに修復'); self.apply_button.setObjectName('primary')
        self.apply_button.clicked.connect(self.execute); buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    def progress(self,path,stage):
        self.status.setText(path+' / '+stage); self.status.repaint()

    def execute(self):
        self.apply_button.setEnabled(False); self.close_button.setEnabled(False)
        for widget in (self.split,self.create,self.axis,self.unfold): widget.setEnabled(False)
        try:
            if self.owner.report: self.owner._before_edit()
            results=repair_uv(self.states,self.create.isChecked(),self.axis.currentData(),self.split.isChecked(),self.progress,unfold=self.unfold.isChecked())
            changed=[r['path'] for r in results if r['changed']]
            if self.owner.report and changed: self.owner._after_edit(changed)
            elif self.owner.live.once: self.owner.live.reset()
            self.summary.setPlainText('\n\n'.join('{}\n  頂点 {} → {} / {} 面・四角形 {} 面を維持\n'
                '  面の頂点位置: 一致 / Unfold事前検証: PASS'.format(r['path'],r['before_vertices'],r['after_vertices'],r['faces'],r['quads']) for r in results))
            self.status.setText(('修復・展開完了。' if self.unfold.isChecked() else '修復完了。Mayaで「展開」を実行できます。')+
                '修復後の再投影・マージ・一括修復で再発した場合は、再度この修復を実行してください。')
        except Exception as exc:
            if self.owner.live.once: self.owner.live.reset()
            self.status.setText('修復できません: '+str(exc))
        finally: self.close_button.setEnabled(True)
