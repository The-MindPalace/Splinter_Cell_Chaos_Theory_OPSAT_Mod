"""Fisher's playbook - how the owner plays, as code. Each skill is a closed loop on game memory.

House style (from the player):
  * crouched the whole game, never standing unless a skill needs it
  * slow: 10-20% walk speed exploring, 2-5% with guards around; never 0 (stopping = letting go of W)
  * assess threats first, then move; path offsets keep Sam out of view cones
  * jump only when the way forward is a ledge (blocked and the goal is above)
  * guards: get behind, grab (Space), interrogate if offered, knock out (right mouse), carry the body to a
    dark spot nobody passes and nobody looks at, verify it stays unseen
  * emergencies: suspicious -> freeze in shadow; alert but blind -> break line of sight and wait;
    alert and facing close -> strike; alarm / several alert -> give up the run (reload)

Every skill returns a short result string; the options environment (options_env.py) turns those into
learnable choices, and fisher.py runs the playbook alone as a baseline.
"""
import heapq
import json
import math
import os
import time
from collections import defaultdict, deque

from .controls import RMB, click, key, tap, wheel
from .game import Game, co
from .explore import ExploreMap, SECTORS, cell_of, heading_of
from .navmesh import NavMesh

FLOOR_M = 2.5
DARK = 5.0                # LuminosityFactor at or below this = shadow
LIT = 15.0
SPEED = {'clear': 0.15, 'near': 0.08, 'close': 0.04, 'danger': 0.02}   # fraction of Sam's walk-speed range
# Measured 2026-10-05 (crouched): the wheel moves walk speed from 0.61 to 1.22 m/s in steps and saturates
# 8 ticks above the slowest. Speed is not readable from memory, so it is tracked open-loop from a clamp.
SPEED_TICKS = 8
MEM_DIR = os.path.join(os.path.expanduser('~'), 'Saved Games', 'OPSAT', 'runs')
GRABBED, KO, CARRIED = 's_Grabbed', ('s_Unconscious', 's_Stunned', 's_Groggy'), 's_Carried'


def flat(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1]) / 100


def heading_to(src, dst):
    return math.degrees(math.atan2(dst[1] - src[1], dst[0] - src[0])) % 360


def ang_diff(a, b):
    return (a - b + 180) % 360 - 180


class WorldMemory:
    """What Sam has learned about this mission: dark spots he stood in, where each guard walks and looks,
    and body spots that turned out bad. Persisted per mission so later runs reuse it."""

    def __init__(self, mission):
        self.path = os.path.join(MEM_DIR, 'fisher_memory_%s.json' % mission)
        self.dark, self.bad_dumps = [], []
        self.tracks = defaultdict(lambda: deque(maxlen=600))      # guard id -> (t, x, y, z, yaw)
        self.walk = {}                 # cell -> [x, y, z]: ground Sam has stood on (70 cm cells)
        self.frontier_fail = {}        # cell -> failed attempts to push on from there
        try:
            d = json.load(open(self.path))
            self.dark, self.bad_dumps = d.get('dark', []), d.get('bad_dumps', [])
            self.walk = {self.cell(p): p for p in d.get('walk', [])}
            self.frontier_fail = {tuple(json.loads(k)): v for k, v in d.get('frontier_fail', {}).items()}
            for gid, pts in d.get('tracks', {}).items():
                self.tracks[gid].extend(pts)
        except (OSError, ValueError):
            pass

    @staticmethod
    def cell(p):
        return (round(p[0] / 70), round(p[1] / 70), round(p[2] / 120))

    def note(self, s, light):
        now = time.time()
        (x, y, z), _ = s['sam']
        c = self.cell((x, y, z))
        if c not in self.walk:
            self.walk[c] = [round(x), round(y), round(z)]
        if light <= DARK and all(flat((x, y), p) > 1.5 for p in self.dark[-400:]):
            self.dark.append([round(x), round(y), round(z), round(light, 1)])
        for g in s['intel'].get('guards', []):
            t = self.tracks[str(g.get('id'))]
            if not t or now - t[-1][0] > 1.0:
                t.append([round(now, 1), round(g['loc'][0]), round(g['loc'][1]), round(g['loc'][2]), g['yaw']])

    def neighbours(self, c):
        x, y, z = c
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    n = (x + dx, y + dy, z + dz)
                    if n != c and n in self.walk:
                        yield n, math.hypot(dx, dy) * 0.7 + abs(dz) * 0.5

    def plan(self, start, target):
        """Frontier on known ground: the walked cell that best trades distance-to-target against path
        length and past failures, and the path to it over walked cells. None if nothing is known."""
        if not self.walk:
            return None
        sc = self.cell(start)
        if sc not in self.walk:
            sc = min(self.walk, key=lambda c: flat(self.walk[c], start) + abs(self.walk[c][2] - start[2]) / 100)
        dist, prev, pq = {sc: 0.0}, {sc: None}, [(0.0, sc)]
        while pq:
            d, c = heapq.heappop(pq)
            if d > dist[c] or d > 150:
                continue
            for n, w in self.neighbours(c):
                if d + w < dist.get(n, 1e9):
                    dist[n], prev[n] = d + w, c
                    heapq.heappush(pq, (d + w, n))
        fails = [(self.walk[f], n) for f, n in self.frontier_fail.items() if f in self.walk]

        def score(c):
            p = self.walk[c]
            near_fail = sum(n for q, n in fails if flat(p, q) < 2.5)   # a failure taints its whole area
            return flat(p, target) + abs(p[2] - target[2]) / 200 + 0.15 * dist[c] + 6.0 * near_fail
        goal = min(dist, key=score)
        path, c = [], goal
        while c is not None:
            path.append(self.walk[c])
            c = prev[c]
        return goal, path[::-1]

    def watched_spot(self, p, horizon_s=300, radius_m=15):
        """Has any guard walked within 10 m of p, or had p inside his view cone within radius_m, recently?"""
        now = time.time()
        for pts in self.tracks.values():
            for t, x, y, z, yaw in pts:
                if now - t > horizon_s or abs(z - p[2]) > FLOOR_M * 100:
                    continue
                d = flat((x, y), p)
                if d < 10 or (d < radius_m and abs(ang_diff(heading_to((x, y), p), yaw / 65536 * 360)) < 35):
                    return True
        return False

    def save(self):
        try:
            os.makedirs(MEM_DIR, exist_ok=True)
            json.dump({'dark': self.dark[-2000:], 'bad_dumps': self.bad_dumps,
                       'tracks': {k: list(v)[-300:] for k, v in self.tracks.items()},
                       'walk': list(self.walk.values()),
                       'frontier_fail': {json.dumps(list(k)): v for k, v in self.frontier_fail.items()}},
                      open(self.path, 'w'))
        except OSError:
            pass


class Fisher:
    def __init__(self, game=None, log=print):
        self.game = game or Game()
        self.log = log
        self.speed_ticks = None        # [current tick above slowest, SPEED_TICKS]
        self.detour = None             # (angle, until) - keep sliding along a wall after an escape
        self.climb_fails = []          # spots where a climb did not work
        self.carrying = None
        self.bodies = []               # [(x, y, z), time dropped]
        s = self.perceive()
        self.mem = WorldMemory(s['mission'] if s else 'unknown')

    # --- perception -------------------------------------------------------------------------------
    def perceive(self):
        s = self.game.snapshot()
        if not s:
            return None
        pawn = self.game.g.m.u32(self.game.g.players[0] + self.game.g.pawn_off)
        s['speed_uu'] = self.game.g._f32(pawn + 744)               # Pawn.GroundSpeed
        sam = s['sam']
        th = []
        for g in s['intel'].get('guards', []):
            mood = co.guard_mood(g)[0]
            d, bearing, dz = co.relative(sam, g['loc'])
            th.append({'g': g, 'mood': mood, 'd': d, 'dz': dz, 'bearing': bearing,
                       'sees': co.facing(g['loc'], g['yaw'], max(g.get('cone') or 60, 60), 3000, sam[0])
                       and abs(dz) < FLOOR_M, 'state': g['state']})
        s['threats'] = sorted(th, key=lambda t: t['d'])
        if hasattr(self, 'mem'):
            self.mem.note(s, s['light'])
            if time.monotonic() - getattr(self, 'saved_at', 0) > 30:   # survive a killed run
                self.saved_at = time.monotonic()
                self.mem.save()
        return s

    def level(self, s):
        """Assess before moving: how dangerous is the spot right now?"""
        live = [t for t in s['threats'] if t['mood'] not in ('DEAD', 'OUT') and abs(t['dz']) < FLOOR_M]
        if any(t['sees'] and t['d'] < 15 and s['light'] > DARK for t in live):
            return 'danger'
        if any(t['d'] < 10 or (t['sees'] and t['d'] < 20) for t in live):
            return 'close'
        if any(t['d'] < 20 for t in live):
            return 'near'
        return 'clear'

    def emergency(self, s, prev_alarm=0):
        """None, or (kind, threat): abort | fight | break_los | suspicious | body_found."""
        live = [t for t in s['threats'] if t['mood'] not in ('DEAD', 'OUT')]
        alert = [t for t in live if t['mood'] == 'ALERT' and t['d'] < 40]
        if s['alarm'] > prev_alarm or len(alert) >= 2:
            return 'abort', None
        if any(str(t['g'].get('event', '')).startswith('AI_BARK_POKE_DEAD') for t in live):
            return 'body_found', None
        for t in alert:
            return ('fight' if t['sees'] and t['d'] < 6 else 'break_los'), t
        susp = [t for t in live if t['mood'] == 'SUSPICIOUS' and t['d'] < 25]
        if susp:
            return 'suspicious', susp[0]
        return None

    # --- body controls -----------------------------------------------------------------------------
    def keep_crouched(self, s):
        if not s['crouched'] and not self.carrying:
            self.game.release_all()
            tap('crouch', 0.12)
            time.sleep(0.4)

    def calibrate_speed(self):
        """Clamp to the slowest walk (the wheel saturates), then track ticks from there."""
        wheel(-(SPEED_TICKS + 12))
        self.speed_ticks = [0, SPEED_TICKS]

    def set_speed(self, frac):
        if getattr(self, 'fast', False):
            frac = 1.0                                   # mapping mode (cheats on): full crouched pace
        if self.speed_ticks is None:
            self.calibrate_speed()
        cur, top = self.speed_ticks
        want = max(0, min(top, round(frac * top)))      # 0 = slowest walk, never standing still
        if want != cur:
            wheel(want - cur)
            self.speed_ticks[0] = want

    def turn_smooth(self, deg, rate=220.0):
        """Turn the camera by deg in small steps (no snapping): about `rate` degrees per second."""
        steps = max(1, int(abs(deg) / 3))
        for _ in range(steps):
            self.game.turn(deg / steps)
            time.sleep(abs(deg) / steps / rate)

    def face(self, target, s, max_step=7.0):
        """Steer toward target while walking: dead zone 4 deg, at most max_step deg per call (~70 deg/s at
        10 calls/s), so the camera glides instead of twitching."""
        (x, y, _), yaw = s['sam']
        err = ang_diff(heading_to((x, y), target), yaw / 65536 * 360)
        if abs(err) > 4:
            self.turn_smooth(max(-max_step, min(max_step, err * 0.5)))
        return err

    def face_heading(self, heading):
        """Stand still and turn smoothly to an absolute heading (degrees)."""
        for _ in range(3):
            yaw = self.perceive()['sam'][1] / 65536 * 360
            err = ang_diff(heading, yaw)
            if abs(err) < 4:
                return
            self.turn_smooth(err)
            time.sleep(0.1)

    # --- movement ------------------------------------------------------------------------------------
    def offset_waypoint(self, s, target):
        """Path offset: if the straight line to target crosses a guard's view cone within 15 m, aim for a
        point beside the guard on his blind side instead."""
        (x, y, z), _ = s['sam']
        for t in s['threats']:
            if t['mood'] in ('DEAD', 'OUT') or abs(t['dz']) > FLOOR_M or t['d'] > 15:
                continue
            gx, gy, _ = t['g']['loc']
            gyaw = t['g']['yaw'] / 65536 * 360
            # closest point of our segment to the guard
            vx, vy = target[0] - x, target[1] - y
            L2 = vx * vx + vy * vy or 1
            u = max(0, min(1, ((gx - x) * vx + (gy - y) * vy) / L2))
            px, py = x + u * vx, y + u * vy
            if flat((px, py), (gx, gy)) < 12 and abs(ang_diff(heading_to((gx, gy), (px, py)), gyaw)) < 40:
                back = math.radians(gyaw + 180)
                return gx + math.cos(back) * 350, gy + math.sin(back) * 350   # 3.5 m behind him
        return target

    def climb(self):
        """Jump only for a ledge: push forward, jump once, check Sam went up."""
        z0 = self.perceive()['sam'][0][2]
        key('w', False)
        time.sleep(0.3)
        tap('jump', 0.12)
        time.sleep(1.4)
        key('w', True)
        z1 = self.perceive()['sam'][0][2]
        ok = z1 - z0 > 50
        self.log('climb %s (%+.0f cm)' % ('ok' if ok else 'failed', z1 - z0))
        if ok:
            self.game.set_crouch(True)                # C is a toggle: check the state, never blind-tap
        return ok

    def unstick(self, s, target):
        """Not getting closer. Walk around it first (four escape headings at full pace, then wall-follow);
        jump only as the last resort - every walking direction blocked and the goal is above (a ledge)."""
        (x, y, z), _ = s['sam']
        p = (x, y, z)
        # stuck again and again in the same few metres while the goal is above: that is a ledge
        self.stalls = [(q, t) for q, t in getattr(self, 'stalls', []) if time.monotonic() - t < 120]
        self.stalls.append((p, time.monotonic()))
        ledge = target[2] - z > 120 and sum(1 for q, _ in self.stalls if flat(q, p) < 2.5) >= 3
        if not ledge:
            speed = self.speed_ticks[0] if self.speed_ticks else 0
            self.set_speed(1.0)                      # test escapes at full pace, then back to sneaking
            try:
                if self._escape(x, y):
                    return True
            finally:
                self.set_speed(speed / SPEED_TICKS)
        if target[2] - z > 60 and not any(flat(c, p) < 1.5 for c in self.climb_fails):
            for ang in (0, 60, -60, 120, -120):          # boxed in: the way on is up
                self.turn_smooth(ang)
                time.sleep(0.15)
                if self.climb():
                    return True
                self.turn_smooth(-ang)
            self.climb_fails.append(p)
        return False

    def _escape(self, x, y):
        for ang in (55, -55, 110, -110):
            self.turn_smooth(ang)
            time.sleep(0.15)
            p0 = self.perceive()['sam'][0]
            self.game.hold({'w'})
            time.sleep(1.2)
            self.game.release_all()
            p1 = self.perceive()['sam'][0]
            if flat(p1, p0) > 0.5:
                self.detour = (ang * 0.6, time.monotonic() + 3.0)
                self.log('escape %+d deg' % ang)
                return True
            self.turn_smooth(-ang)                   # back to the original heading before the next try
            time.sleep(0.1)
        self.log('boxed in at (%.0f, %.0f)' % (x, y))
        return False

    def move_to(self, target, budget_s=4.0, tol_m=0.8, react=True):
        """Advance toward target (x, y, z) for up to budget_s, house style. Returns arrived | moving |
        stuck | emergency:<kind>."""
        t0, prev_alarm = time.monotonic(), None
        best, best_t = None, time.monotonic()
        result = 'moving'
        try:
            while time.monotonic() - t0 < budget_s:
                s = self.perceive()
                if s is None:
                    return 'dead'
                prev_alarm = s['alarm'] if prev_alarm is None else prev_alarm
                if react:
                    e = self.emergency(s, prev_alarm)
                    if e:
                        result = 'emergency:' + e[0]
                        break
                if flat(s['sam'][0], target) < tol_m:
                    result = 'arrived'
                    break
                self.keep_crouched(s)
                lvl = self.level(s)
                if lvl == 'danger':                       # lit and watched: never freeze in light
                    self.game.release_all()
                    result = 'emergency:exposed'
                    break
                self.set_speed(SPEED[lvl])
                way = self.offset_waypoint(s, target)
                if self.detour and time.monotonic() < self.detour[1]:   # keep sliding along the wall
                    (x, y, _), _ = s['sam']
                    h = math.radians(heading_to((x, y), way) + self.detour[0])
                    way = (x + math.cos(h) * 300, y + math.sin(h) * 300)
                err = self.face(way, s)
                if abs(err) > 45:                              # big turn: stop, turn on the spot
                    self.game.release_all()
                    self.face_heading(heading_to(s['sam'][0][:2], way))
                    continue
                self.game.hold({'w'} if abs(err) < 25 else set())
                d = flat(s['sam'][0], target)                 # stuck = not getting closer, even if moving
                if best is None or d < best - 0.25:
                    best, best_t = d, time.monotonic()
                elif time.monotonic() - best_t > 2.0:
                    self.game.release_all()
                    if not self.unstick(self.perceive(), target):
                        result = 'stuck'
                        break
                    best, best_t = None, time.monotonic()
                time.sleep(0.1)
        finally:
            self.game.release_all()
        return result

    def advance(self, target, budget_s=3.0):
        """ADVANCE with memory: push straight at the target; after a failed push, walk the known ground to
        the best frontier and probe outward from it, biased toward the target. Failed frontiers are marked
        so the next attempt pushes somewhere new."""
        if getattr(self, 'blocked_target', None) != target[:2]:
            before = flat(self.perceive()['sam'][0], target)
            r = self.move_to(target, budget_s=budget_s)
            gained = before - flat(self.perceive()['sam'][0], target)
            if r == 'stuck' or (r == 'moving' and gained < 0.5):   # sliding around without getting closer
                self.no_gain = getattr(self, 'no_gain', 0) + 1
                if r == 'stuck' or self.no_gain >= 2:
                    self.blocked_target, self.no_gain = target[:2], 0
            else:
                self.no_gain = 0
            return r
        s = self.perceive()
        plan = self.mem.plan(s['sam'][0], target)
        if not plan:
            self.blocked_target = None
            return self.move_to(target, budget_s=budget_s)
        cell, path = plan
        for wp in path[1::3][:6] + [path[-1]]:          # follow the trail (every ~2 m) to the frontier
            r = self.move_to(wp, budget_s=4.0, tol_m=0.9)
            if r.startswith('emergency') or r == 'dead':
                return r
        before = flat(self.perceive()['sam'][0], target)
        r = self.move_to(target, budget_s=budget_s + 2)  # push on from the frontier
        after = flat(self.perceive()['sam'][0], target)
        if after > before - 0.5:
            self.mem.frontier_fail[cell] = self.mem.frontier_fail.get(cell, 0) + 1
            self.log('frontier %s failed (%d)' % (path[-1][:2], self.mem.frontier_fail[cell]))
        else:
            self.blocked_target = None                    # new ground: straight pushes again
        return r

    # --- exploration (explore.py: every attempt recorded, finite search) ------------------------------
    def explorer(self):
        if getattr(self, '_xmap', None) is None:
            mission = self.perceive()['mission'] or 'unknown'
            self._xmap = ExploreMap(mission)
            # known-good paths: every recorded run of this mission (the player's and the bot's), cheats excluded
            repo_runs = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                     'training', 'runs')
            paths = [os.path.join(d, f) for d in (MEM_DIR, repo_runs) if os.path.isdir(d)
                     for f in sorted(os.listdir(d)) if f.startswith(mission + '_') and f.endswith('.jsonl')]
            self.log('explore map: %d moves learned from %d recorded runs' % (
                self._xmap.seed_from_tracks(paths), len(paths)))
            self._xmap.add_reverses()
            self.log('explore map: %d neighbouring-cell guesses' % self._xmap.add_adjacent())
        return self._xmap

    def _walk_sector(self, sector, max_s=2.5, min_m=0.8):
        """From where Sam stands, face the sector heading and walk; result open/blocked + where it led."""
        x = self.explorer()
        s = self.perceive()
        start = s['sam'][0]
        c0 = x.visit(start, s['room'])
        self.face_heading(heading_of(sector))
        self.set_speed(SPEED['near'])
        t0 = time.monotonic()
        self.game.hold({'w'})
        try:
            while time.monotonic() - t0 < max_s:
                time.sleep(0.15)
                s = self.perceive()
                e = self.emergency(s)
                if e:
                    return 'emergency:' + e[0], c0, None
                self.keep_crouched(s)
                c = x.visit(s['sam'][0], s['room'])
                if c != c0 and flat(s['sam'][0], start) >= min_m:
                    return 'open', c0, c
        finally:
            self.game.release_all()
        return 'blocked', c0, None

    def _known_move(self, c, sec):
        """Repeat a known move precisely: walk to the recorded position of the next cell; for a climb, go to
        the exact spot it was done from, face the same way, jump. open | blocked | climb_ok | climb_fail."""
        x = self.explorer()
        t = x.tries[(c, sec)]
        to = tuple(t['to'])
        if t['result'] == 'climb_ok':
            spot = t.get('at') or x.cells[c]['p']
            r = self.move_to(spot, budget_s=4.0, tol_m=0.35)
            if r.startswith('emergency'):
                return r
            self.game.release_all()
            self.face_heading(t.get('yaw', sec * 360 / SECTORS))
            z0 = self.perceive()['sam'][0][2]
            self.climb()
            time.sleep(0.6)
            s = self.perceive()
            x.visit(s['sam'][0], s['room'])
            return 'climb_ok' if s['sam'][0][2] - z0 > 50 else 'climb_fail'
        r = self.move_to(x.cells[to]['p'], budget_s=3.5, tol_m=0.45)
        if r.startswith('emergency'):
            return r
        s = self.perceive()
        here = x.visit(s['sam'][0], s['room'])
        return 'open' if here == to or flat(s['sam'][0], x.cells[to]['p']) < 0.7 else 'blocked'

    def _climb_sector(self, sector):
        x = self.explorer()
        s = self.perceive()
        c0 = x.visit(s['sam'][0], s['room'])
        self.face_heading(heading_of(sector))
        ok = self.climb()
        s = self.perceive()
        c = x.visit(s['sam'][0], s['room'])
        return ('climb_ok' if ok and c != c0 else 'climb_fail'), c0, (c if ok else None)

    def explore_step(self, goal_room, target):
        """One step toward entering goal_room (by the 3D map's room names)."""
        return self.explore_to(target, lambda p, room: room == goal_room, goal_room)

    def explore_to(self, target, is_goal, label='goal'):
        """One step toward ground where is_goal(position, room) holds: walk a known route if there is one,
        otherwise run the most promising untried experiment and write down the result. Returns entered |
        progress | dead_end | emergency:<kind>."""
        x = self.explorer()
        s = self.perceive()
        if is_goal(s['sam'][0], s['room']):
            return 'entered'
        here = x.visit(s['sam'][0], s['room'])
        sam = s['sam'][0]
        # watchdog: decisions that leave Sam where he was (some branch returning without acting) are counted;
        # after 4 the known-path logic is skipped and a fresh experiment is run from where he stands
        last = getattr(self, '_xpos', None)
        self._xidle = getattr(self, '_xidle', 0) + 1 if last and flat(last, sam) < 0.2 else 0
        self._xpos = sam
        stuck = self._xidle >= 4
        if stuck:
            self.log('watchdog: %d decisions without moving; fresh experiment from here' % self._xidle)
            self._xidle = 0
        goal_cells = {c for c, v in x.cells.items() if is_goal(v['p'], v.get('room'))}
        plan = x.route(here, goal_cells) if goal_cells and not stuck else None
        if not goal_cells and not stuck:
            # nothing known reaches the goal yet: walk the known way to the known cell closest to the target
            # (e.g. the recorded route up into the Cavern), and explore onward from there
            dist, _ = x.reachable(here)
            near_t = lambda p: math.hypot(p[0] - target[0], p[1] - target[1]) + 2 * abs(p[2] - target[2])
            best = min(dist, key=lambda c: near_t(x.cells[c]['p']) if c in x.cells else 1e18)
            if best != here and best in x.cells and near_t(x.cells[best]['p']) < near_t(sam) - 200:
                plan = x.route(here, {best})
        if not plan and goal_cells and not stuck:
            # standing within 1 m of a known cell counts as being on it (cell boundaries are arbitrary)
            near = sorted((flat(v['p'], sam), c) for c, v in x.cells.items()
                          if c != here and flat(v['p'], sam) < 1.0 and abs(v['p'][2] - sam[2]) < 80)
            for _, c in near:
                p2 = x.route(c, goal_cells)
                if p2:
                    plan, here = p2, c
                    break
        if not plan and goal_cells and not stuck:
            # not on a known path: step onto the nearest one that leads there (within 4 m), else explore
            # toward it rather than toward the room's centre (which may be behind a cliff)
            sam = s['sam'][0]
            lead = [(flat(x.cells[c]['p'], sam) + abs(x.cells[c]['p'][2] - sam[2]) / 100, c)
                    for c in x.leads_to(goal_cells) - goal_cells if x.unreach.get(c, 0) < 2]
            if lead:
                d, c = min(lead)
                if d < 4 and abs(x.cells[c]['p'][2] - sam[2]) < 100:
                    r = self.move_to(x.cells[c]['p'], budget_s=4.0, tol_m=0.4)
                    if r.startswith('emergency'):
                        return r
                    s2 = self.perceive()
                    if x.visit(s2['sam'][0], s2['room']) != c and flat(s2['sam'][0], x.cells[c]['p']) > 0.8:
                        x.unreach[c] = x.unreach.get(c, 0) + 1   # twice and this cell is not offered again
                        self.log('could not step onto known path at %s (%d)' % (c, x.unreach[c]))
                        x.save()
                    return 'progress'
                target = x.cells[c]['p']
        if plan:
            for c, sec in plan[:6]:                         # walk the known way, a few moves per decision
                r = self._known_move(c, sec)
                if r.startswith('emergency'):
                    return r
                if r not in ('open', 'climb_ok'):
                    x.record(c, sec, r, detail='known route failed')
                    break
                x.record(c, sec, r, x.tries[(c, sec)]['to'], detail='known route')
            x.save()
            s = self.perceive()
            return 'entered' if is_goal(s['sam'][0], s['room']) else 'progress'
        exp = (stuck and x.next_experiment(here, target, climb_ok=True, only_start=True)) or             x.next_experiment(here, target, climb_ok=True, room=s['room'])
        if not exp:
            x.save()
            self.log('dead end: every reachable heading tried (%s)' % x.stats())
            return 'dead_end'
        cell, sector, mode, path = exp
        for c, sec in path:                                  # go to the experiment's cell over known moves
            r = self._known_move(c, sec)
            if r.startswith('emergency'):
                return r
            if r not in ('open', 'climb_ok'):
                x.record(c, sec, r, detail='path to experiment failed')
                x.save()
                return 'progress'
        s = self.perceive()
        if x.visit(s['sam'][0], s['room']) != cell:          # not where the experiment belongs: do not file
            x.unreach[cell] = x.unreach.get(cell, 0) + 1     # its result under another cell; replan instead
            x.save()
            return 'progress'
        if mode == 'walk':
            r, c0, to = self._walk_sector(sector)
        else:
            r, c0, to = self._climb_sector(sector)
        if r.startswith('emergency'):
            return r
        x.record(c0, sector, r, to, detail='%s toward %s' % (mode, label))
        self.log('experiment %s heading %d from %s: %s' % (mode, heading_of(sector), c0, r))
        x.save()
        s = self.perceive()
        return 'entered' if is_goal(s['sam'][0], s['room']) else 'progress'

    # --- navigation on the level's own AI mesh (navmesh.py) --------------------------------------------
    def nav(self):
        if getattr(self, '_nav', None) is None:
            self._nav = NavMesh.read(self.game.g)
            cr = self._nav.crossings()
            self.log('nav mesh: %d triangles, %d areas, %d crossings' % (
                sum(len(t) for t in self._nav.tris), len(self._nav._comps), len(cr) // 2))
        return self._nav

    def _follow(self, pts, n=4):
        """Walk a few mesh waypoints, house style. arrived | progress | stuck | emergency:<kind>."""
        r = 'progress'
        for wp in pts[:n]:
            r = self.move_to(wp, budget_s=4.0, tol_m=0.6)
            if r.startswith('emergency') or r == 'stuck':
                return r
        return 'arrived' if len(pts) <= n and r == 'arrived' else 'progress'

    def nav_step(self, goal):
        """One step toward goal (x, y, z): on the mesh, walk the guards' paths; between areas, walk to the
        crossing and solve the short hop with the explorer (aimed at the other side); off the mesh, explore
        toward the best entry point. arrived | progress | stuck | dead_end | emergency:<kind>."""
        nm = self.nav()
        idx = nm.piece_index()
        s = self.perceive()
        sam = s['sam'][0]
        gnode = nm.locate(goal) or nm.nearest(goal, max_dz=1500)[0]
        gp = idx[gnode]
        node = nm.locate(sam)
        if node is None:
            # off the mesh: the entry point that is close and starts a short area route to the goal
            best = None
            for k, pts in nm.border_points().items():
                route = nm.piece_route(k, gp)
                if route is None:
                    continue
                q = min(pts, key=lambda p: math.hypot(p[0] - sam[0], p[1] - sam[1]) + 2 * abs(p[2] - sam[2]))
                cost = math.hypot(q[0] - sam[0], q[1] - sam[1]) + 2 * abs(q[2] - sam[2]) + 600 * (len(route) - 1)
                if best is None or cost < best[0]:
                    best = (cost, k, q)
            if best is None:
                return 'dead_end'
            _, k, q = best
            if getattr(self, '_entry_log', None) != k:
                self._entry_log = k
                self.log('off the mesh: heading for %s at %s' % (nm.names[nm._comps[k] and next(iter(nm._comps[k]))[0]],
                                                              [round(v) for v in q]))
            r = self.explore_to(q, lambda p, room: nm.locate(p) is not None, 'mesh')
            return r
        sp = idx[node]
        if sp == gp:
            pts = nm.path(sam, goal) or [goal]
            return self._follow(pts)
        route = nm.piece_route(sp, gp)
        if not route:
            return 'dead_end'
        pa, pb, _ = nm.crossings()[(route[0], route[1])]
        nxt = route[1]
        if math.hypot(pa[0] - sam[0], pa[1] - sam[1]) > 120:
            pts = nm.path(sam, pa) or [pa]
            return self._follow(pts)
        # at the crossing: try straight over first (stairs, door), then explore the hop
        r = self.move_to(pb, budget_s=5.0, tol_m=0.6)
        if r.startswith('emergency'):
            return r
        if idx.get(nm.locate(self.perceive()['sam'][0])) == nxt:
            return 'progress'
        return self.explore_to(pb, lambda p, room: idx.get(nm.locate(p)) == nxt, nm.names[next(iter(nm._comps[nxt]))[0]])

    # --- hiding ---------------------------------------------------------------------------------------
    def dark_spot(self, s, away_from=None, max_m=25):
        (x, y, z), _ = s['sam']
        best = None
        for p in self.mem.dark:
            d = flat((x, y), p)
            if d > max_m or abs(p[2] - z) > FLOOR_M * 100:
                continue
            if any(t['mood'] not in ('DEAD', 'OUT') and flat(t['g']['loc'], p) < 8 for t in s['threats']):
                continue
            score = d - (flat(away_from, p) * 0.7 if away_from else 0)
            if best is None or score < best[0]:
                best = (score, p)
        return best[1] if best else None

    def hide(self, threat=None, wait_s=6.0):
        s = self.perceive()
        spot = self.dark_spot(s, threat['g']['loc'] if threat else None)
        if spot:
            self.set_speed(SPEED['close'])
            self.move_to(spot, budget_s=8.0, react=False)
        self.game.release_all()
        t0 = time.monotonic()
        while time.monotonic() - t0 < wait_s:               # hold still in the dark until it calms down
            s = self.perceive()
            if not self.emergency(s):
                return 'hidden'
            time.sleep(0.3)
        return 'hidden_still_hot'

    # --- emergency playbook -----------------------------------------------------------------------------
    def handle(self, kind, threat):
        if kind == 'abort':
            return 'abort'                                   # alarm / several alert: reload the checkpoint
        if kind == 'suspicious':
            s = self.perceive()
            if s['light'] > DARK:
                return self.hide(threat)
            # in shadow: freeze; if he walks past with his back to us, take him
            t0 = time.monotonic()
            while time.monotonic() - t0 < 10:
                s = self.perceive()
                near = [t for t in s['threats'] if t['g'].get('id') == threat['g'].get('id')]
                if near and near[0]['d'] < 2.5 and not near[0]['sees']:
                    return self.takedown(near[0])
                if not self.emergency(s):
                    return 'calm'
                time.sleep(0.3)
            return self.hide(threat)
        if kind in ('break_los', 'exposed', 'body_found'):
            return self.hide(threat, wait_s=20.0)
        if kind == 'fight':                                  # alert, facing, close: close in and strike
            s = self.perceive()
            for _ in range(20):
                t = next((t for t in s['threats'] if t['g'].get('id') == threat['g'].get('id')), None)
                if not t or t['mood'] in ('OUT', 'DEAD'):
                    return 'neutralised'
                if t['d'] < 1.6:
                    self.face(t['g']['loc'], s)
                    click(RMB)
                    time.sleep(0.6)
                else:
                    self.move_to(t['g']['loc'], budget_s=0.6, tol_m=1.4, react=False)
                s = self.perceive()
            return 'fight_lost'
        return 'ignored'

    # --- guards ---------------------------------------------------------------------------------------
    def takedown_target(self, s, max_m=15):
        """A calm, conscious guard on our level, not looking at us."""
        for t in s['threats']:
            if t['mood'] == 'CALM' and t['d'] < max_m and abs(t['dz']) < FLOOR_M and not t['sees'] \
                    and t['state'] not in (GRABBED, CARRIED) + KO:
                return t
        return None

    def takedown(self, t, interrogate=True):
        """Get behind him (offset path), grab with Space, interrogate if offered, knock out (right mouse)."""
        gid = t['g'].get('id')
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:
            s = self.perceive()
            cur = next((x for x in s['threats'] if x['g'].get('id') == gid), None)
            if not cur or cur['mood'] in ('DEAD', 'OUT'):
                return 'target_lost'
            if cur['sees'] or cur['mood'] != 'CALM':
                return self.handle(*(self.emergency(s) or ('suspicious', cur)))
            gx, gy, gz = cur['g']['loc']
            gyaw = math.radians(cur['g']['yaw'] / 65536 * 360)
            behind = (gx - math.cos(gyaw) * 110, gy - math.sin(gyaw) * 110, gz)
            if flat(s['sam'][0], behind) > 0.6:
                self.set_speed(SPEED['danger'] if cur['d'] < 4 else SPEED['close'])
                self.move_to(behind, budget_s=0.8, tol_m=0.5, react=False)
                continue
            self.face((gx, gy), s)
            time.sleep(0.15)
            tap('use', 0.1)                                  # grab
            time.sleep(0.8)
            held = self._guard(gid)
            if not held or held['state'] != GRABBED:
                self.log('grab missed (state %s)' % (held['state'] if held else '?'))
                continue
            if interrogate and self.prompt_rows() >= 2:      # an interrogation is on offer
                tap('use', 0.1)
                time.sleep(6.0)                              # let the conversation play out
            click(RMB)                                       # non-lethal knock-out
            time.sleep(1.2)
            g = self._guard(gid)
            ok = g and g['state'] in KO
            self.log('takedown %s' % ('done' if ok else 'unclear (state %s)' % (g['state'] if g else '?')))
            return 'ko' if ok else 'grab_unclear'
        return 'timeout'

    def _guard(self, gid):
        s = self.perceive()
        return next((t for t in s['threats'] if t['g'].get('id') == gid), None)

    # --- bodies ---------------------------------------------------------------------------------------
    def dump_spot(self, s, max_m=30):
        """Dark, unwatched, away from everybody: light <= DARK where Sam stood, no guard within 15 m now,
        no guard track within 10 m and no view cone on it in the last 5 minutes, not a known bad spot."""
        (x, y, z), _ = s['sam']
        best = None
        for p in self.mem.dark:
            d = flat((x, y), p)
            if d > max_m or abs(p[2] - z) > FLOOR_M * 100 or p[3] > DARK - 1:
                continue
            if any(flat(b, p) < 6 for b in self.mem.bad_dumps):
                continue
            if any(t['mood'] not in ('DEAD', 'OUT') and flat(t['g']['loc'], p) < 15 for t in s['threats']):
                continue
            if self.mem.watched_spot(p):
                continue
            if best is None or d < best[0]:
                best = (d, p)
        return best[1] if best else None

    def dump_body(self, gid):
        s = self.perceive()
        body = self._guard(gid)
        if not body:
            return 'no_body'
        spot = self.dump_spot(s)
        if not spot:
            return 'no_safe_spot'                            # keep looking: explore more dark first
        self.move_to(body['g']['loc'], budget_s=6, tol_m=1.0, react=False)
        self.face(body['g']['loc'], self.perceive())
        tap('use', 0.1)                                      # pick up
        time.sleep(1.2)
        b = self._guard(gid)
        if not b or b['state'] != CARRIED:
            return 'pickup_failed'
        self.carrying = gid
        r = self.move_to(spot, budget_s=25, tol_m=0.8)
        tap('use', 0.1)                                      # drop
        time.sleep(1.0)
        self.carrying = None
        self.game.set_crouch(True)
        self.bodies.append((tuple(spot[:3]), time.time(), gid))
        self.log('body dropped at %s (%s)' % (spot[:2], r))
        return 'dumped'

    def audit_bodies(self, s):
        """Verify dumped bodies stay unseen: a guard within 8 m with the body in his cone marks the spot bad."""
        for spot, _, gid in self.bodies:
            for t in s['threats']:
                if t['mood'] in ('DEAD', 'OUT'):
                    continue
                gx, gy, _ = t['g']['loc']
                if flat((gx, gy), spot) < 8 and abs(ang_diff(heading_to((gx, gy), spot),
                                                           t['g']['yaw'] / 65536 * 360)) < 35:
                    if list(spot) not in self.mem.bad_dumps:
                        self.mem.bad_dumps.append(list(spot))
                        self.log('body at %s is in view: spot marked bad' % (spot[:2],))
                    return 'body_in_view'
        return 'ok'

    # --- HUD -----------------------------------------------------------------------------------------
    def prompt_rows(self):
        """How many entries the top-right interaction list shows (0 = none). Reads the HUD box: rows of
        bright text inside the frame at the top right of the game window. Calibrate on first live run."""
        import numpy as np
        from PIL import ImageGrab
        import ctypes
        import ctypes.wintypes as wt
        r = wt.RECT()
        ctypes.windll.user32.GetWindowRect(self.game.hwnd, ctypes.byref(r))
        W, H = r.right - r.left, r.bottom - r.top
        box = (r.left + int(W * .745), r.top + int(H * .11), r.left + int(W * .98), r.top + int(H * .42))
        a = np.asarray(ImageGrab.grab(bbox=box).convert('L'), np.float32)
        rows = (a > 170).mean(axis=1) > 0.02                # rows containing bright HUD text
        bands = int(np.sum(rows[1:] & ~rows[:-1]))
        return max(0, bands - 1)                             # minus the "INTERACT" header

    def save(self):
        self.mem.save()
