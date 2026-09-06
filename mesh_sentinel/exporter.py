"""Portable UTF-8 reports, written atomically."""
import csv
import html
import io
import os
import tempfile

from .model import RULE_MAP


def _safe_cell(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def export_report(report, path):
    extension = os.path.splitext(path)[1].lower()
    if extension == ".json":
        content = report.to_json()
    elif extension == ".csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(("record", "mesh", "rule", "severity", "component", "ids", "status", "message"))
        writer.writerow(("report", "", "", "", "", "", "complete" if report.complete else "incomplete", report.created))
        writer.writerow(("settings", "", "", "", "", "", report.scope,
                         "distance={} cm; area={} cm^2; aspect={}; pair_budget={}".format(
                             report.settings.distance, report.settings.area, report.settings.aspect, report.settings.pair_budget)))
        for error in report.errors:
            writer.writerow(("error", "", "", "", "", "", "failed", _safe_cell(error)))
        for mesh in report.meshes:
            for key, status in mesh.checks.items():
                writer.writerow(("check", _safe_cell(mesh.path), key, "", "", "", status, ""))
            for note in mesh.notes:
                writer.writerow(("note", _safe_cell(mesh.path), "", "", "", "", "", _safe_cell(note)))
            for f in mesh.findings:
                writer.writerow(tuple(_safe_cell(v) for v in ("finding", f.mesh, f.rule, f.severity,
                    f.component, " ".join(map(str, f.ids)), mesh.checks.get(f.rule, ""), f.message)))
        content = "\ufeff" + stream.getvalue()
    elif extension in (".html", ".htm"):
        esc = lambda s: html.escape(str(s), quote=True)
        rows = []
        for m in report.meshes:
            for f in m.findings:
                rows.append("<tr><td>{}</td><td class='{}'>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    esc(m.path), esc(f.severity), esc(f.severity), esc(RULE_MAP[f.rule].title),
                    len(f.ids), esc(f.component + ": " + ", ".join(map(str, f.ids))), esc(f.message)))
        checks = "".join("<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(esc(m.path), esc(RULE_MAP[k].title), esc(v))
                         for m in report.meshes for k, v in m.checks.items())
        notes = "".join("<li>{}</li>".format(esc(n)) for n in report.errors + [n for m in report.meshes for n in m.notes])
        status = "検査完了" if report.complete else "未完了・要確認"
        content = """<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Mesh Sentinel — Inspection Report</title><style>
body{{background:#10141b;color:#e6edf5;font:14px/1.7 system-ui,sans-serif;margin:48px}}
h1{{font-size:36px;letter-spacing:-1px}}small{{color:#75d9bd}}p{{color:#a7b5c8}}
table{{border-collapse:collapse;width:100%;margin:20px 0 40px}}th,td{{border-bottom:1px solid #2c3542;padding:12px;text-align:left;overflow-wrap:anywhere;max-width:400px}}
th{{background:#1c2430}}.error{{color:#ff848b}}.warning{{color:#eebd6a}}.review{{color:#91b6ff}}
@media print{{body{{background:white;color:#222;margin:15px}}th{{background:#eee}}p,small{{color:#333}}}}
</style><small>GEOMETRY QUALITY / MESH SENTINEL</small><h1>メッシュ検査レポート</h1>
<p>{created} · v{version} · {status} · {scope} · {count} meshes</p>
<p>距離 {distance:g} cm / 面積 {area:g} cm² / アスペクト比 {aspect:g} / 比較上限 {budget:,}（項目・メッシュごと）</p>
<p>内部面・法線は要確認の候補。異なるメッシュ間の交差は検査対象外。件数は各検出グループのコンポーネント数です。</p>
<ul>{notes}</ul><h2>検出結果</h2><table><tr><th>Mesh</th><th>Severity</th><th>Check</th><th>Count</th><th>Components</th><th>Details</th></tr>{rows}</table>
<h2>検査の実行状態</h2><table><tr><th>Mesh</th><th>Check</th><th>Status</th></tr>{checks}</table></html>""".format(
            created=esc(report.created), version=esc(report.version), status=status, scope=esc(report.scope),
            count=len(report.meshes), distance=report.settings.distance, area=report.settings.area,
            aspect=report.settings.aspect, budget=report.settings.pair_budget, notes=notes, rows="".join(rows), checks=checks)
    else:
        raise ValueError("対応形式は JSON / CSV / HTML です")
    directory = os.path.dirname(os.path.abspath(path))
    fd, temp = tempfile.mkstemp(prefix=".sentinel-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
