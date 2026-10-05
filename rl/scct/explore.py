"""Exploration that learns the level and cannot loop forever.

The level becomes a grid of 1 m cells. From each cell Sam can try 8 headings. Every attempt is an
experiment with a recorded outcome, appended to attempts_<mission>.jsonl and kept in the map:
    open        walked at least 0.8 m that way (and the cell it led to)
    blocked     pushed but did not get through
    climb_ok    blocked on foot, a jump got Sam up (ledge)
    climb_fail  blocked, and a jump did not help either
Rules:
  * known open moves are a graph; reaching the goal room over known moves = plan and walk it
  * otherwise pick the untried (cell, heading) whose result would bring Sam closest to the goal, walk there
    over known moves, try it, write down the result
  * each (cell, heading) is tried at most twice on foot, climbs only where walking was blocked and the goal is
    higher - so the search is finite: when every heading reachable has been tried, report a dead end
  * the map persists per mission: the next episode starts with everything learned, failures included
"""
import heapq
import json
import math
import os
import time

CELL = 100.0                                   # cm
SECTORS = 8
MEM_DIR = os.path.join(os.path.expanduser('~'), 'Saved Games', 'OPSAT', 'runs')


def cell_of(p):
    return (round(p[0] / CELL), round(p[1] / CELL), round(p[2] / 150))


def heading_of(sector):
    return sector * 360.0 / SECTORS


class ExploreMap:
    def __init__(self, mission):
        self.mission = mission
        self.path = os.path.join(MEM_DIR, 'explore_%s.json' % mission)
        self.log_path = os.path.join(MEM_DIR, 'attempts_%s.jsonl' % mission)
        self.cells = {}        # cell -> {'p': [x, y, z], 'room': name}
        self.tries = {}        # (cell, sector) -> {'result': ..., 'n': tries, 'to': cell or None}
        self.unreach = {}      # cell -> times Sam tried to step onto it and could not
        try:
            d = json.load(open(self.path))
            self.cells = {tuple(json.loads(k)): v for k, v in d['cells'].items()}
            self.tries = {(tuple(json.loads(k)[0]), json.loads(k)[1]): v for k, v in d['tries'].items()}
            self.unreach = {tuple(json.loads(k)): v for k, v in d.get('unreach', {}).items()}
        except (OSError, ValueError, KeyError):
            pass

    def save(self):
        try:
            os.makedirs(MEM_DIR, exist_ok=True)
            json.dump({'cells': {json.dumps(list(k)): v for k, v in self.cells.items()},
                       'tries': {json.dumps([list(c), s]): v for (c, s), v in self.tries.items()},
                       'unreach': {json.dumps(list(k)): v for k, v in self.unreach.items()}},
                      open(self.path, 'w'))
        except OSError:
            pass

    def visit(self, p, room):
        c = cell_of(p)
        if c not in self.cells:
            self.cells[c] = {'p': [round(p[0]), round(p[1]), round(p[2])], 'room': room}
        return c

    def record(self, c, sector, result, to=None, detail=''):
        t = self.tries.setdefault((c, sector), {'result': None, 'n': 0, 'to': None})
        t['n'] += 1
        if result in ('open', 'climb_ok'):
            t['fail'] = 0
            if t['result'] not in ('open', 'climb_ok'):
                t['result'], t['to'] = result, (list(to) if to else None)
        elif t['result'] in ('open', 'climb_ok'):
            t['fail'] = t.get('fail', 0) + 1           # a known move failed: twice in a row and it is dropped
        else:
            t['result'], t['to'] = result, (list(to) if to else None)
        if result == 'open' and to:
            self._reverse(c, sector, tuple(to))
        try:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({'t': round(time.time(), 1), 'cell': list(c), 'heading': heading_of(sector),
                                    'result': result, 'to': list(to) if to else None, 'n': t['n'],
                                    'detail': detail}) + '\n')
        except OSError:
            pass

    def seed_from_tracks(self, paths, max_step_m=2.5, max_dt=3.0):
        """Recorded runs (runlog tracks, 2 Hz) are proof of where Sam can walk: every step between two
        consecutive samples becomes a known open move. Stretches with cheats on ('x') prove nothing and are
        skipped. Returns the number of moves learned."""
        n = 0
        for path in paths:
            n += self._seed_climbs(path)
            prev = None
            try:
                lines = open(path, encoding='utf-8').readlines()
            except OSError:
                continue
            for line in lines:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if 'p' not in d:
                    continue
                if d.get('x'):
                    prev = None
                    continue
                p = d['p']
                c = self.visit(p[:3], d.get('r'))
                if prev and d['t'] - prev[0] <= max_dt and c != prev[1]:
                    pp = prev[2]
                    if math.hypot(p[0] - pp[0], p[1] - pp[1]) <= max_step_m * 100 and abs(p[2] - pp[2]) < 120:
                        sec = round(math.degrees(math.atan2(p[1] - pp[1], p[0] - pp[0])) / 45) % SECTORS
                        moves = [(prev[1], sec, c)]
                        if abs(p[2] - pp[2]) < 60:              # level ground walks both ways (not drops)
                            moves.append((c, (sec + SECTORS // 2) % SECTORS, prev[1]))
                        for frm, sc, to in moves:
                            t = self.tries.setdefault((frm, sc), {'result': None, 'n': 0, 'to': None})
                            if t['result'] not in ('open', 'climb_ok'):
                                t['result'], t['to'], t['src'] = 'open', list(to), 'track'
                                n += 1
                prev = (d['t'], c, p)
        return n

    def _seed_climbs(self, path):
        """Jumps in a recorded run that got Sam onto something: up 60+ cm within 1.2 s and still up 2 s later
        (jumping on the spot lands back down and does not count). Kept as climb moves with the exact spot
        and facing, because a ledge climb only works from the right place."""
        try:
            smp = [d for d in (json.loads(l) for l in open(path, encoding='utf-8')) if 'p' in d]
        except (OSError, ValueError):
            return 0
        n = 0
        for i in range(len(smp) - 1):
            a, b = smp[i], smp[i + 1]
            if a.get('x') or b['t'] - a['t'] > 1.2 or b['p'][2] - a['p'][2] < 60:
                continue
            later = next((d for d in smp[i + 1:] if d['t'] >= a['t'] + 2.0), None)
            if not later or later['t'] - a['t'] > 4 or later['p'][2] - a['p'][2] < 60 or later.get('x'):
                continue
            pa, pl = a['p'], later['p']
            if math.hypot(pl[0] - pa[0], pl[1] - pa[1]) > 300:
                continue
            frm, to = self.visit(pa[:3], a.get('r')), self.visit(pl[:3], later.get('r'))
            if frm == to:
                continue
            yaw = (pa[3] / 65536 * 360) % 360
            t = self.tries.setdefault((frm, round(yaw / 45) % SECTORS), {'result': None, 'n': 0, 'to': None})
            if t['result'] != 'climb_ok':
                t.update(result='climb_ok', to=list(to), src='track', at=pa[:3], yaw=round(yaw, 1))
                n += 1
        return n

    def leads_to(self, goal_cells):
        """Cells with a known walk to any goal cell (reverse search over known moves)."""
        back = {}
        for (c, s), t in self.tries.items():
            if t['result'] in ('open', 'climb_ok') and t['to'] and t.get('fail', 0) < 2:
                back.setdefault(tuple(t['to']), []).append(c)
        seen, todo = set(goal_cells), list(goal_cells)
        while todo:
            for pc in back.get(todo.pop(), []):
                if pc not in seen:
                    seen.add(pc)
                    todo.append(pc)
        return seen

    def _reverse(self, c, sector, to):
        """Level ground walks both ways: walking c -> to also proves to -> c (not after a drop)."""
        a, b = self.cells.get(c, {}).get('p'), self.cells.get(to, {}).get('p')
        if not a or not b or abs(a[2] - b[2]) >= 60:
            return
        t = self.tries.setdefault((to, (sector + SECTORS // 2) % SECTORS), {'result': None, 'n': 0, 'to': None})
        if t['result'] not in ('open', 'climb_ok'):
            t['result'], t['to'], t['src'] = 'open', list(c), 'reverse'

    def add_reverses(self):
        """Back-fill the way back for every open walk already in the map (maps saved before this rule)."""
        for (c, sec), t in list(self.tries.items()):
            if t['result'] == 'open' and t['to']:
                self._reverse(c, sec, tuple(t['to']))

    def edges(self, c):
        for s in range(SECTORS):
            t = self.tries.get((c, s))
            if t and t['result'] in ('open', 'climb_ok') and t['to'] and t.get('fail', 0) < 2:
                yield tuple(t['to']), s, (1.0 if t['result'] == 'open' else 3.0)

    def route(self, start, goal_cells):
        """Shortest known walk from start to any goal cell: list of (cell, sector, how) or None."""
        dist, prev, pq = {start: 0.0}, {start: None}, [(0.0, start)]
        while pq:
            d, c = heapq.heappop(pq)
            if c in goal_cells:
                out = []
                while prev[c]:
                    pc, s = prev[c]
                    out.append((pc, s))
                    c = pc
                return out[::-1]
            if d > dist[c]:
                continue
            for n, s, w in self.edges(c):
                if d + w < dist.get(n, 1e9):
                    dist[n], prev[n] = d + w, (c, s)
                    heapq.heappush(pq, (d + w, n))
        return None

    def reachable(self, start):
        dist, prev, pq = {start: 0.0}, {start: None}, [(0.0, start)]
        while pq:
            d, c = heapq.heappop(pq)
            if d > dist[c]:
                continue
            for n, s, w in self.edges(c):
                if d + w < dist.get(n, 1e9):
                    dist[n], prev[n] = d + w, (c, s)
                    heapq.heappush(pq, (d + w, n))
        return dist, prev

    def next_experiment(self, start, target, climb_ok):
        """The untried (cell, heading) most likely to get closer to target, among cells reachable over known
        moves. Returns (cell, sector, mode, path_to_cell) or None when everything reachable is exhausted."""
        dist, prev = self.reachable(start)
        best = None
        for c, d in dist.items():
            p = self.cells.get(c, {}).get('p')
            if not p:
                continue
            for s in range(SECTORS):
                t = self.tries.get((c, s))
                h = math.radians(heading_of(s))
                ahead = (p[0] + math.cos(h) * 150, p[1] + math.sin(h) * 150)
                gain = math.hypot(target[0] - ahead[0], target[1] - ahead[1]) / 100
                if t is None or (t['result'] == 'blocked' and t['n'] < 2):
                    mode = 'walk'
                elif t['result'] == 'blocked' and climb_ok and target[2] - p[2] > 80:
                    mode = 'climb'
                else:
                    continue
                cost = gain + 0.4 * d + (1.5 if t else 0.0) + (2.0 if mode == 'climb' else 0.0)
                if best is None or cost < best[0]:
                    best = (cost, c, s, mode)
        if not best:
            return None
        _, c, s, mode = best
        path, x = [], c
        while prev[x]:
            pc, ps = prev[x]
            path.append((pc, ps))
            x = pc
        return c, s, mode, path[::-1]

    def stats(self):
        res = {}
        for t in self.tries.values():
            res[t['result']] = res.get(t['result'], 0) + 1
        return {'cells': len(self.cells), 'experiments': sum(t['n'] for t in self.tries.values()), **res}
