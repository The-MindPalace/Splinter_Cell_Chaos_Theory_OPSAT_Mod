"""The level's own AI navigation mesh, read from game memory: the walkable map, no exploring needed.

SCCT guards path on navigation meshes (class ENavMesh, one per area: NavMeshActor 'NM_NM_Entrance_LGT_1',
'NM_NM_Dungeon_LGT_0', ...). The data is native (no script properties), laid out as (found by probing):
    ENavMesh + 0x50   TArray<FVector>  vertices (x, y, z floats, 12 bytes)
    ENavMesh + 0x68   TArray<tri>      triangles, 60 bytes each:
                      int v0, v1, v2; int n0, n1, n2 (neighbour triangle across each edge, -1 = border);
                      float centre x, y, z; 24 bytes runtime scratch
Meshes are separate objects; where two meshes' border edges meet (both ends within 40 cm) they are joined.

Guards do not climb, so the mesh covers guard ground (courtyards, corridors, rooms) but not Sam-only routes
(the beach, crawlspaces, pipes, ledges). Off the mesh, the explorer (explore.py) still finds the way - now
aimed at the nearest mesh point instead of a room centre behind a cliff.

  python -m scct.navmesh        dump stats + save Saved Games/OPSAT/runs/navmesh_<mission>.json
"""
import heapq
import json
import math
import os
import struct

from .game import co

VERTS_OFF, TRIS_OFF, TRI_SIZE = 0x50, 0x68, 60
JOIN_CM, ON_MESH_Z = 40.0, 120.0
OUT_DIR = os.path.join(os.path.expanduser('~'), 'Saved Games', 'OPSAT', 'runs')


def _tarray(m, addr):
    b = m.read(addr, 8)
    return struct.unpack('<II', b) if b else (0, 0)


class NavMesh:
    def __init__(self, verts, tris, names):
        self.verts = verts          # list per mesh of [(x, y, z)]
        self.tris = tris            # list per mesh of [((v0, v1, v2), (n0, n1, n2), (cx, cy, cz))]
        self.names = names          # mesh index -> area name
        self.links = {}             # (mesh, tri) -> [(mesh, tri)] across mesh borders
        self._join()

    # --- reading ---------------------------------------------------------------------------------------
    @classmethod
    def read(cls, g):
        """g: cheat_overlay.Game. Reads every ENavMesh in the loaded level."""
        objs = g.objects()
        meshes = [o for o in objs if g.oname(g.m.u32(o + co.O_CLASS)) == 'ENavMesh']
        # area names: NavMeshActor.NavMesh -> mesh
        names = {}
        nm_off = None
        for o in objs:
            hdr = g.m.read(o + co.O_OUTER, 0x10)
            if not hdr:
                continue
            outer, _, nm, c = struct.unpack('<IIII', hdr)
            if g.oname(c) == 'ObjectProperty' and g.name(nm) == 'NavMesh' and g.oname(outer) == 'NavMeshActor':
                nm_off = g.m.u32(o + co.P_OFFSET)
        if nm_off is not None:
            for o in objs:
                if g.oname(g.m.u32(o + co.O_CLASS)) == 'NavMeshActor':
                    names[g.m.u32(o + nm_off)] = g.oname(o).replace('NM_NM_', '')
        verts, tris, labels = [], [], []
        for o in meshes:
            vp, vn = _tarray(g.m, o + VERTS_OFF)
            tp, tn = _tarray(g.m, o + TRIS_OFF)
            vb, tb = g.m.read(vp, 12 * vn) if vn else b'', g.m.read(tp, TRI_SIZE * tn) if tn else b''
            if not vb or not tb:
                continue
            v = [struct.unpack_from('<3f', vb, 12 * i) for i in range(vn)]
            t = []
            for i in range(tn):
                a = struct.unpack_from('<6i3f', tb, TRI_SIZE * i)
                t.append((a[0:3], a[3:6], a[6:9]))
            if all(0 <= k < vn for tri in t for k in tri[0]):
                verts.append(v)
                tris.append(t)
                labels.append(names.get(o, g.oname(o)))
        return cls(verts, tris, labels)

    # --- structure -------------------------------------------------------------------------------------
    def _join(self):
        """Border edges of different meshes that coincide (both ends within JOIN_CM) become links."""
        border = []
        for m, ts in enumerate(self.tris):
            for i, (vs, ns, _) in enumerate(ts):
                for e in range(3):
                    if ns[e] == -1:
                        a, b = self.verts[m][vs[e]], self.verts[m][vs[(e + 1) % 3]]
                        border.append((m, i, a, b))
        cell = {}
        for k, (m, i, a, b) in enumerate(border):
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            cell.setdefault((round(mid[0] / 200), round(mid[1] / 200)), []).append(k)
        d = lambda p, q: math.dist(p, q)
        for k, (m, i, a, b) in enumerate(border):
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            cx, cy = round(mid[0] / 200), round(mid[1] / 200)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for k2 in cell.get((cx + dx, cy + dy), []):
                        m2, i2, a2, b2 = border[k2]
                        if m2 == m:
                            continue
                        if min(max(d(a, a2), d(b, b2)), max(d(a, b2), d(b, a2))) < JOIN_CM:
                            self.links.setdefault((m, i), []).append((m2, i2))

    def neighbours(self, node):
        m, i = node
        for n in self.tris[m][i][1]:
            if n >= 0:
                yield (m, n)
        yield from self.links.get(node, [])

    def centre(self, node):
        return self.tris[node[0]][node[1]][2]

    def corners(self, node):
        m, i = node
        return [self.verts[m][k] for k in self.tris[m][i][0]]

    # --- queries ---------------------------------------------------------------------------------------
    def locate(self, p, max_dz=ON_MESH_Z):
        """The triangle under point p (x, y, z), or None if p is not on the mesh."""
        best = None
        for m, ts in enumerate(self.tris):
            vs = self.verts[m]
            for i, (ix, _, c) in enumerate(ts):
                if abs(c[2] - p[2]) > max_dz + 300:
                    continue
                a, b, cc = vs[ix[0]], vs[ix[1]], vs[ix[2]]
                w = _bary(p, a, b, cc)
                if w is None:
                    continue
                z = w[0] * a[2] + w[1] * b[2] + w[2] * cc[2]
                dz = abs(z - p[2])
                if dz <= max_dz and (best is None or dz < best[0]):
                    best = (dz, (m, i))
        return best[1] if best else None

    def nearest(self, p, max_dz=400.0):
        """Closest triangle centre to p (3D, height weighted double): (node, distance cm)."""
        best = None
        for m, ts in enumerate(self.tris):
            for i, (_, _, c) in enumerate(ts):
                if abs(c[2] - p[2]) > max_dz:
                    continue
                d = math.hypot(c[0] - p[0], c[1] - p[1]) + 2 * abs(c[2] - p[2])
                if best is None or d < best[1]:
                    best = ((m, i), d)
        return best

    def path(self, a, b):
        """Waypoints from point a to point b over the mesh (both must be on it): [(x, y, z), ...] or None.
        A* over triangles; waypoints are the midpoints of the edges crossed, then b."""
        s, t = self.locate(a), self.locate(b)
        if s is None or t is None:
            return None
        if s == t:
            return [tuple(b)]
        h = lambda n: math.dist(self.centre(n), self.centre(t))
        dist, prev, pq = {s: 0.0}, {s: None}, [(h(s), s)]
        while pq:
            _, n = heapq.heappop(pq)
            if n == t:
                break
            for nb in self.neighbours(n):
                nd = dist[n] + math.dist(self.centre(n), self.centre(nb))
                if nd < dist.get(nb, 1e18):
                    dist[nb], prev[nb] = nd, n
                    heapq.heappush(pq, (nd + h(nb), nb))
        if t not in prev:
            return None
        chain, n = [], t
        while n is not None:
            chain.append(n)
            n = prev[n]
        chain.reverse()
        pts = []
        for n1, n2 in zip(chain, chain[1:]):
            shared = [v for v in self.corners(n1) if any(math.dist(v, w) < JOIN_CM for w in self.corners(n2))]
            if len(shared) >= 2:
                pts.append(tuple((shared[0][k] + shared[1][k]) / 2 for k in range(3)))
            else:
                pts.append(self.centre(n2))
        pts.append(tuple(b))
        return pts

    def components(self):
        """Connected pieces of the mesh: list of node sets (largest first)."""
        seen, comps = set(), []
        for m, ts in enumerate(self.tris):
            for i in range(len(ts)):
                if (m, i) in seen:
                    continue
                comp, todo = set(), [(m, i)]
                while todo:
                    n = todo.pop()
                    if n in comp:
                        continue
                    comp.add(n)
                    todo.extend(x for x in self.neighbours(n) if x not in comp)
                seen |= comp
                comps.append(comp)
        return sorted(comps, key=len, reverse=True)

    # --- areas and the crossings between them -----------------------------------------------------------
    def piece_index(self):
        if getattr(self, '_piece', None) is None:
            self._comps = self.components()
            self._piece = {n: k for k, c in enumerate(self._comps) for n in c}
        return self._piece

    def piece_of(self, p):
        n = self.locate(p)
        return self.piece_index().get(n) if n else None

    def border_points(self):
        """Midpoints of border edges per piece: {piece: [(x, y, z)]}."""
        if getattr(self, '_border', None) is None:
            idx, out = self.piece_index(), {}
            for m, ts in enumerate(self.tris):
                for i, (vs, ns, _) in enumerate(ts):
                    for e in range(3):
                        if ns[e] == -1:
                            a, b = self.verts[m][vs[e]], self.verts[m][vs[(e + 1) % 3]]
                            out.setdefault(idx[(m, i)], []).append(tuple((a[k] + b[k]) / 2 for k in range(3)))
            self._border = out
        return self._border

    def crossings(self, max_gap=900.0, min_tris=4):
        """Where areas meet: for each pair of pieces, the closest pair of border points (horizontal gap under
        max_gap). {(a, b): (point in a, point in b, cost)} in both directions. Cost favours short, level
        crossings (a stairway or door) over big height changes (a climb)."""
        if getattr(self, '_cross', None) is not None:
            return self._cross
        bp = {k: v for k, v in self.border_points().items() if len(self._comps[k]) >= min_tris}
        keys, out = sorted(bp), {}
        for ii, a in enumerate(keys):
            for b in keys[ii + 1:]:
                best = None
                for p in bp[a]:
                    for q in bp[b]:
                        h = math.hypot(p[0] - q[0], p[1] - q[1])
                        if h < max_gap:
                            dz = abs(p[2] - q[2])
                            # stairs and ramps rise no steeper than their run; steeper is a wall to climb
                            c = h + 2.0 * dz + (3000.0 if dz > 1.2 * h + 60 else 0.0)
                            if best is None or c < best[2]:
                                best = (p, q, c)
                if best:
                    out[(a, b)] = best
                    out[(b, a)] = (best[1], best[0], best[2])
        self._cross = out
        return out

    def piece_route(self, a, b):
        """Pieces from a to b over crossings (Dijkstra; crossing cost + 500 per area change), or None."""
        cr = self.crossings()
        dist, prev, pq = {a: 0.0}, {a: None}, [(0.0, a)]
        while pq:
            d, k = heapq.heappop(pq)
            if k == b:
                out = []
                while k is not None:
                    out.append(k)
                    k = prev[k]
                return out[::-1]
            if d > dist[k]:
                continue
            for (x, y), (_, _, c) in cr.items():
                if x == k and d + c + 500 < dist.get(y, 1e18):
                    dist[y], prev[y] = d + c + 500, k
                    heapq.heappush(pq, (d + c + 500, y))
        return None

    def save(self, mission):
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, 'navmesh_%s.json' % mission)
        json.dump({'names': self.names, 'verts': [[[round(c, 1) for c in v] for v in vs] for vs in self.verts],
                   'tris': [[[list(t[0]), list(t[1]), [round(c, 1) for c in t[2]]] for t in ts] for ts in self.tris],
                   'links': [[list(k), [list(x) for x in v]] for k, v in self.links.items()]}, open(path, 'w'))
        return path


def _bary(p, a, b, c):
    """Barycentric weights of p in triangle abc (XY), or None if outside (5 cm slack)."""
    d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if abs(d) < 1e-6:
        return None
    w1 = ((b[1] - c[1]) * (p[0] - c[0]) + (c[0] - b[0]) * (p[1] - c[1])) / d
    w2 = ((c[1] - a[1]) * (p[0] - c[0]) + (a[0] - c[0]) * (p[1] - c[1])) / d
    w3 = 1 - w1 - w2
    slack = 5.0 / max(1.0, math.sqrt(abs(d)))
    if min(w1, w2, w3) < -slack:
        return None
    return w1, w2, w3


if __name__ == '__main__':
    g = co.Game(co.Mem(co.find_pid()))
    mission = g.mission_state()[0] or 'unknown'
    nm = NavMesh.read(g)
    comps = nm.components()
    print('meshes', len(nm.tris), 'triangles', sum(len(t) for t in nm.tris), 'cross-mesh links', len(nm.links))
    print('areas', nm.names)
    print('connected pieces', len(comps), 'sizes', [len(c) for c in comps[:10]])
    print('saved', nm.save(mission))
