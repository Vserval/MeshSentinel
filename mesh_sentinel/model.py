"""Serializable contracts. No Maya or Qt dependencies."""
from dataclasses import dataclass, field, asdict
from typing import Tuple, Dict, List
import hashlib
import json
import math


@dataclass(frozen=True)
class Rule:
    key: str
    title: str
    severity: str
    description: str


RULES = (
    Rule("nonmanifold", "非多様体", "error", "3面以上を共有するエッジ、面の接続が分岐する頂点を検出。"),
    Rule("degenerate", "縮退面", "error", "面積が閾値以下、頂点の繰り返し、無効な三角形分割を検出。"),
    Rule("zero_edge", "ゼロ長エッジ", "error", "両端の距離が距離許容誤差以下のエッジを検出。"),
    Rule("duplicate_vertex", "重複頂点", "warning", "異なる頂点IDが距離許容誤差以内にある組を検出。意図したシームも対象。"),
    Rule("duplicate_face", "重複面", "error", "頂点の巡回順が同じ、または逆向きで、対応座標が許容誤差以内の面を検出。"),
    Rule("internal", "内部面の候補", "review", "閉じた多様体シェル内に完全に入る別の面を検出。意図した内殻も候補。"),
    Rule("intersection", "自己交差", "error", "同一メッシュ内の三角形の交差と共面の面積重複を検出。正常な共有辺・頂点の接触を除外。"),
    Rule("normals", "法線反転・不整合", "review", "隣接面の巻き順不整合、閉殻の内向き、面と逆向きのコーナー法線を検出。意図の確認が必要。"),
    Rule("boundary", "穴・開いたエッジ", "warning", "1面のみが接続する境界エッジと、Mayaが穴付きと判定した面を検出。"),
    Rule("t_junction", "Tジャンクション", "warning", "エッジに接続していない頂点がエッジの内部に位置する候補を検出。"),
    Rule("sliver", "細長い三角形", "warning", "三角形面の最長辺÷最小高度が閾値以上の面を検出。"),
    Rule("ngon", "N-gon", "warning", "5頂点以上の面を検出。"),
    Rule("concave", "凹N-gon", "warning", "5頂点以上で凹形状の面を検出。MayaのisConvex判定を使用。"),
)
RULE_MAP = {r.key: r for r in RULES}


@dataclass(frozen=True)
class Settings:
    distance: float = 0.00001  # Maya API world units: centimeters
    area: float = 0.0000000001
    aspect: float = 100.0
    pair_budget: int = 2000000
    enabled: Tuple[str, ...] = tuple(r.key for r in RULES)

    def __post_init__(self):
        for name in ("distance", "area", "aspect"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("{} must be finite and positive".format(name))
        if self.pair_budget < 1 or any(k not in RULE_MAP for k in self.enabled):
            raise ValueError("Invalid budget or rule key")


@dataclass(frozen=True)
class MeshData:
    path: str
    uuid: str
    points: tuple
    faces: tuple
    edges: tuple  # Native Maya edge order; (vertexA, vertexB).
    triangles: tuple  # (faceID, vertexA, vertexB, vertexC), Maya triangulation.
    concave_faces: tuple = ()
    holed_faces: tuple = ()
    invalid_faces: tuple = ()
    opposing_normals: tuple = ()
    fingerprint: str = ""
    edge_incidence: tuple = ()  # Optional native loop topology, aligned with edges.

    def signature(self):
        payload = (self.uuid, self.points, self.faces, self.edges, self.triangles,
                   self.concave_faces, self.holed_faces, self.invalid_faces, self.opposing_normals, self.edge_incidence)
        return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


@dataclass
class Finding:
    rule: str
    mesh: str
    component: str
    ids: List[int]
    message: str
    severity: str = ""

    def __post_init__(self):
        if not self.severity:
            self.severity = RULE_MAP[self.rule].severity


@dataclass
class MeshResult:
    path: str
    uuid: str
    fingerprint: str
    vertices: int
    faces: int
    findings: List[Finding] = field(default_factory=list)
    checks: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    seconds: float = 0.0


@dataclass
class Report:
    created: str
    settings: Settings
    meshes: List[MeshResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    cancelled: bool = False
    version: str = "1.7.0-rc1"
    scope: str = "selected"

    @property
    def complete(self):
        return bool(self.meshes) and not self.errors and not self.cancelled and all(
            s in ("complete", "disabled") for m in self.meshes for s in m.checks.values())

    def to_dict(self):
        data = asdict(self)
        data["complete"] = self.complete
        data["units"] = {"distance": "cm (world space)", "area": "cm^2"}
        data["rules"] = {r.key: asdict(r) for r in RULES}
        return data

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
