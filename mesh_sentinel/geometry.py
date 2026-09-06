"""Double precision geometry and AABB tree, independent of host APIs."""
import math


def sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def add(a, b):
    return (a[0]+b[0], a[1]+b[1], a[2]+b[2])


def mul(a, s):
    return (a[0]*s, a[1]*s, a[2]*s)


def dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def length(a):
    return math.sqrt(dot(a, a))


def normal(t):
    return cross(sub(t[1], t[0]), sub(t[2], t[0]))


def bounds(points):
    return (tuple(min(p[i] for p in points) for i in range(3)),
            tuple(max(p[i] for p in points) for i in range(3)))


def overlaps(a, b, eps=0.0):
    lo, hi = a
    other_lo, other_hi = b
    return (lo[0] <= other_hi[0]+eps and other_lo[0] <= hi[0]+eps
            and lo[1] <= other_hi[1]+eps and other_lo[1] <= hi[1]+eps
            and lo[2] <= other_hi[2]+eps and other_lo[2] <= hi[2]+eps)


def segment_distance(p, a, b):
    d = sub(b, a)
    d2 = dot(d, d)
    t = dot(sub(p, a), d) / d2 if d2 else 0.0
    q = add(a, mul(d, max(0., min(1., t))))
    return length(sub(p, q)), t


class BVH:
    """Balanced broad phase. Leaf indices retain the caller's component IDs."""
    def __init__(self, boxes, checkpoint=lambda: None):
        self.boxes = boxes
        self.checkpoint = checkpoint
        self.root = self._build(list(range(len(boxes)))) if boxes else None

    def _build(self, ids):
        self.checkpoint()
        box = (tuple(min(self.boxes[j][0][i] for j in ids) for i in range(3)),
               tuple(max(self.boxes[j][1][i] for j in ids) for i in range(3)))
        if len(ids) <= 12:
            return box, ids, None, None
        axis = max(range(3), key=lambda i: box[1][i] - box[0][i])
        ids.sort(key=lambda j: self.boxes[j][0][axis] + self.boxes[j][1][axis])
        mid = len(ids) // 2
        return box, None, self._build(ids[:mid]), self._build(ids[mid:])

    def query(self, box, eps=0.0):
        stack = [self.root] if self.root else []
        while stack:
            self.checkpoint()
            node = stack.pop()
            if not overlaps(node[0], box, eps):
                continue
            if node[1] is not None:
                for i in node[1]:
                    if overlaps(self.boxes[i], box, eps):
                        yield i
            else:
                stack.extend(node[2:])


def _inside_triangle(p, t, n, eps):
    for i in range(3):
        edge = sub(t[(i + 1) % 3], t[i])
        if dot(cross(edge, sub(p, t[i])), n) < -eps * length(edge):
            return False
    return True


def _plane_slice(t, plane_t, unit_n, eps):
    ds = [dot(sub(p, plane_t[0]), unit_n) for p in t]
    points = []
    for i in range(3):
        j = (i + 1) % 3
        if abs(ds[i]) <= eps and _inside_triangle(t[i], plane_t, unit_n, eps):
            points.append(t[i])
        if ds[i] * ds[j] < 0.:
            p = add(t[i], mul(sub(t[j], t[i]), ds[i] / (ds[i] - ds[j])))
            if _inside_triangle(p, plane_t, unit_n, eps):
                points.append(p)
    return points


def coplanar_area(a, b, n):
    """Clip convex triangles after projection to the dominant normal plane."""
    axis = max(range(3), key=lambda i: abs(n[i]))
    axes = [i for i in range(3) if i != axis]
    project = lambda p: (p[axes[0]] - a[0][axes[0]], p[axes[1]] - a[0][axes[1]])
    poly, clip = list(map(project, a)), list(map(project, b))
    turn = lambda x, y, z: (y[0]-x[0])*(z[1]-x[1])-(y[1]-x[1])*(z[0]-x[0])
    sign = 1 if turn(*clip) >= 0 else -1
    for i in range(3):
        c, d = clip[i], clip[(i + 1) % 3]
        source, poly = poly, []
        if not source:
            return 0.
        for j, p in enumerate(source):
            q = source[(j + 1) % len(source)]
            dp, dq = sign * turn(c, d, p), sign * turn(c, d, q)
            if dp >= 0:
                poly.append(p)
            if (dp >= 0) != (dq >= 0):
                u = dp / (dp - dq)
                poly.append((p[0]+u*(q[0]-p[0]), p[1]+u*(q[1]-p[1])))
    area = abs(sum(poly[i][0]*poly[(i+1)%len(poly)][1]-poly[(i+1)%len(poly)][0]*poly[i][1]
                   for i in range(len(poly)))) * .5
    return area * length(n) / abs(n[axis])


def triangles_intersect(a, b, shared, eps, area_eps):
    na, nb = normal(a), normal(b)
    la, lb = length(na), length(nb)
    if la <= area_eps * 2 or lb <= area_eps * 2:
        return False
    na, nb = mul(na, 1/la), mul(nb, 1/lb)
    da = [dot(sub(p, b[0]), nb) for p in a]
    db = [dot(sub(p, a[0]), na) for p in b]
    if (min(da) > eps or max(da) < -eps or min(db) > eps or max(db) < -eps):
        return False
    if length(cross(na, nb)) <= 1e-10 and max(map(abs, da + db)) <= eps:
        return coplanar_area(a, b, na) > area_eps
    hits = _plane_slice(a, b, nb, eps) + _plane_slice(b, a, na, eps)
    for p in hits:
        if not shared:
            return True
        if len(shared) == 1 and length(sub(p, shared[0])) > eps:
            return True
        if len(shared) == 2 and segment_distance(p, *shared)[0] > eps:
            return True
    return False


def ray_triangle(origin, direction, tri, eps):
    """Return positive distance and edge ambiguity; None means no hit."""
    e1, e2 = sub(tri[1], tri[0]), sub(tri[2], tri[0])
    h = cross(direction, e2)
    det = dot(e1, h)
    if abs(det) <= 1e-12 * max(length(e1)*length(e2), 1e-30):
        return None
    s = sub(origin, tri[0])
    u = dot(s, h)/det
    q = cross(s, e1)
    v = dot(direction, q)/det
    distance = dot(e2, q)/det
    bary_eps = min(1e-4, eps / max(length(e1), length(e2), eps))
    if u < -bary_eps or v < -bary_eps or u+v > 1+bary_eps or distance <= eps:
        return None
    return distance, min(u, v, 1-u-v) <= bary_eps


def inside_shell(point, triangles, tree, eps, tick):
    if not tree.root:
        return False
    box = tree.root[0]
    if any(point[i] <= box[0][i]+eps or point[i] >= box[1][i]-eps for i in range(3)):
        return False
    extent = length(sub(box[1], box[0])) * 3 + eps
    votes = []
    for raw in ((1., .3713907, .694247), (.219373, 1., .517139), (.431173, .293137, 1.)):
        d = mul(raw, 1/length(raw))
        raybox = bounds((point, add(point, mul(d, extent))))
        hits, ambiguous = [], False
        for j in tree.query(raybox, eps):
            tick()
            hit = ray_triangle(point, d, triangles[j], eps)
            if hit:
                hits.append(hit[0])
                ambiguous = ambiguous or hit[1]
        if ambiguous:
            continue
        hits.sort()
        unique = [h for i, h in enumerate(hits) if i == 0 or h-hits[i-1] > eps]
        votes.append(len(unique) % 2 == 1)
    return len(votes) >= 2 and all(votes)
