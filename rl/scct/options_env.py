"""SCCTFisher-v0: the agent chooses *what* to do; Fisher's playbook (skills.py) does it, house style.

Raw keys took thousands of random steps just to walk in a line. Here one decision is one skill:
  0 ADVANCE    stealth-walk toward the next waypoint of the route (crouched, threat-scaled speed, cone offsets)
  1 WAIT       hold still, crouched
  2 HIDE       go to the nearest unwatched dark spot away from guards and wait
  3 TAKEDOWN   sneak behind the nearest calm guard, grab, interrogate if offered, knock out
  4 DUMP       carry the latest unconscious guard to a dark spot nobody passes or looks at
  5 SIDESTEP_L / 6 SIDESTEP_R  short strafe around an obstacle
Emergencies are reflexes, not choices: when a skill reports one, the playbook handles it at once; an alarm
or several alert guards ends the episode (the player's rule: reload).
"""
import time

from gymnasium import spaces

import json
import math
import os

import numpy as np

from .briefing import FLAGS, Briefing
from .env import SCCTNavEnv
from .explore import SECTORS
from .skills import Fisher, KO, flat

OPTIONS = ['ADVANCE', 'WAIT', 'HIDE', 'TAKEDOWN', 'DUMP', 'SIDESTEP_L', 'SIDESTEP_R']


class SCCTFisherEnv(SCCTNavEnv):
    def __init__(self, max_decisions=300, pixels=True, **kw):
        super().__init__(max_steps=max_decisions, pixels=pixels, **kw)
        self.action_space = spaces.Discrete(len(OPTIONS))
        self.fisher = Fisher(self.game, log=lambda *a: print('[fisher]', *a))
        self.brief = Briefing(log=self.fisher.log)
        self.fisher.brief = self.brief
        # the agent also sees what the mission notes say to expect here (FLAGS, 0/1 each)
        if isinstance(self.observation_space, spaces.Dict):
            n = self.observation_space['vec'].shape[0] + len(FLAGS)
            self.observation_space = spaces.Dict({'vec': spaces.Box(-np.inf, np.inf, (n,), np.float32),
                                                  'img': self.observation_space['img']})
        else:
            n = self.observation_space.shape[0] + len(FLAGS)
            self.observation_space = spaces.Box(-np.inf, np.inf, (n,), np.float32)
        self.dead_exits = set()     # (room, next room) pairs exploration proved unreachable
        self.reached = set()

    def reset(self, seed=None, options=None):
        obs, info = super().reset(seed=seed, options=options)
        self.fisher.carrying, self.fisher.bodies = None, []
        self.dead_exits = set()     # each episode retries every exit with what the map has learned since
        snap = self.game.snapshot()
        if snap:
            new_mission = snap.get('mission') != self.brief.mission
            self.brief.update(snap, self.game.g)
            if new_mission:
                self._vent_moves()
        if getattr(self, 'map_mode', False):    # mapping: invincible + invisible, so exploring cannot end it
            self.fisher.log('map mode: cheats (god, invisible) = %s' % (self.game.set_cheats(True),))
        self.reached = set()        # rooms entered this episode: the goal never falls back behind them
        xm = self.fisher.explorer()
        xm.unreach.clear()                       # near-misses are per episode; the map has grown since
        for t in xm.tries.values():              # so are failures of known moves: two per episode, then
            t['fail'] = 0                        # dropped until the next one (an imprecise climb is not a wall)
        self.fisher.save()          # keep what the last episode learned (walkable ground, dark spots)
        self.dumped = set()
        self.alarm0 = self.prev['alarm']
        return obs, info

    def _checkpoint(self, s):
        """First time Sam reaches a room on the route (no guard alert, not falling): quicksave there, so the
        next episodes start at the furthest point instead of replaying the beach. One save per room, ever
        (kept in runs/checkpoints_<mission>.json)."""
        room, mission = s.get('room'), s.get('mission')
        if not room or not mission or room == 'Beach':
            return
        path = os.path.join(os.path.expanduser('~'), 'Saved Games', 'OPSAT', 'runs', 'checkpoints_%s.json' % mission)
        if getattr(self, '_ckpt_mission', None) != mission:
            try:
                self._ckpt = set(json.load(open(path)))
            except (OSError, ValueError):
                self._ckpt = set()
            self._ckpt_mission = mission
        if room in self._ckpt or room not in (self.goal[1] if self.goal else []):
            return
        threats = self.fisher.perceive()['threats']
        if any(t['mood'] in ('ALERT', 'SUSPICIOUS') and t['d'] < 25 for t in threats):
            return
        from .controls import tap
        self.game.release_all()
        time.sleep(0.5)
        tap('quicksave', 0.1)
        time.sleep(1.5)
        self._ckpt.add(room)
        try:
            json.dump(sorted(self._ckpt), open(path, 'w'))
        except OSError:
            pass
        self.fisher.log('checkpoint: quicksaved on reaching %s - episodes start here now' % room)

    def _vec(self, s, goal):
        return np.concatenate([super()._vec(s, goal), np.asarray(self.brief.vector(), np.float32)])

    def _vent_moves(self):
        """Crawlspaces from the interactables: entry <-> exit as known moves that need Space."""
        x = self.fisher.explorer()
        n = 0
        for a, b in self.brief.vents():
            ca, cb = x.visit(a, None), x.visit(b, None)
            for p, q, cp, cq in ((a, b, ca, cb), (b, a, cb, ca)):
                sec = round(math.degrees(math.atan2(q[1] - p[1], q[0] - p[0])) / 45) % SECTORS
                t = x.tries.setdefault((cp, sec), {'result': None, 'n': 0, 'to': None})
                if t['result'] != 'open':
                    t.update(result='open', to=list(cq), src='vent', use=True)
                    n += 1
        if n:
            self.fisher.log('briefing: %d crawlspace moves from vent interactions' % n)

    def _next_room(self, route):
        """The next room to enter: the route's next room unless exploration proved it unreachable from here;
        then the adjacent room with the shortest remaining route to the objective."""
        here, target_room = route[0], route[-1]
        self.reached.add(here)
        # stepping back over a room boundary while exploring must not turn the goal around: aim for the
        # first room on the way that has not been reached yet
        ahead = next((r for r in route[1:] if r not in self.reached), route[-1])
        frontier = route[route.index(ahead) - 1]
        if frontier != here and frontier in self.reached:
            return frontier                      # back to the frontier room first (known route), explore from there
        if (here, ahead) not in self.dead_exits:
            return ahead
        g = self.game.g
        options = []
        for n in g.room_graph.get(here, []):
            if (here, n) in self.dead_exits:
                continue
            rest = g.route(n, target_room)
            if rest:
                options.append((len(rest), n))
        return min(options)[1] if options else None

    def _bodies(self, s):
        return [t for t in self.fisher.perceive()['threats']
                if t['state'] in KO and t['g'].get('id') not in self.dumped and t['d'] < 30]

    def step(self, action):
        self._guard_focus()
        f, name = self.fisher, OPTIONS[int(action)]
        r, note = -0.05, ''
        s = self.prev
        if name == 'ADVANCE' and getattr(self, 'use_mesh', True):
            try:
                route = self.goal[1]                     # rooms from here to the objective's room (3D map)
                nxt = self._next_room(route) if len(route) > 1 else None
                if nxt:                                  # next room on the way: its centre, over the mesh
                    target = self.game.snapshot()['rooms'].get(nxt) or self.goal[2][-1]
                    note = f.nav_step(target, room=nxt)
                    if note == 'entered':
                        self.reached.add(nxt)
                else:                                    # in the objective's room: the beacon
                    note = f.nav_step(self.goal[2][-1])
            except Exception as e:                       # mesh unreadable: fall back to rooms + exploring
                f.log('nav mesh unavailable (%r); exploring by rooms' % (e,))
                self.use_mesh = False
                note = 'progress'
        elif name == 'ADVANCE':
            route = self.goal[1]                         # rooms from here to the objective's room
            if len(route) > 1:                           # next room: explore until we enter it
                nxt = self._next_room(route)
                if nxt is None:
                    note = 'all_exits_dead'
                else:
                    snap = self.game.snapshot()
                    note = f.explore_step(nxt, snap['rooms'].get(nxt, self.goal[2][0]))
                    if note == 'dead_end':
                        self.dead_exits.add((route[0], nxt))
                        f.log('exit %s -> %s marked dead; trying another way' % (route[0], nxt))
            else:                                        # in the objective's room: walk to the beacon
                note = f.move_to(self.goal[2][-1], budget_s=3.0)
        elif name == 'WAIT':
            self.game.release_all()
            time.sleep(1.5)
            note = 'waited'
        elif name == 'HIDE':
            note = f.hide()
        elif name == 'TAKEDOWN':
            t = f.takedown_target(f.perceive())
            note = f.takedown(t) if t else 'no_target'
            r += 2.0 if note == 'ko' else (-0.5 if note == 'no_target' else 0)
        elif name == 'DUMP':
            bodies = self._bodies(s)
            note = f.dump_body(bodies[0]['g'].get('id')) if bodies else 'no_body'
            if note == 'dumped':
                self.dumped.add(bodies[0]['g'].get('id'))
                r += 5.0
            elif note == 'no_body':
                r -= 0.5
        else:
            self.game.hold({'a' if name.endswith('L') else 'd', 'w'})
            time.sleep(0.8)
            self.game.release_all()
            note = 'sidestep'
        if note.startswith('emergency:'):                   # reflex: the playbook's emergency response
            kind = note.split(':', 1)[1]
            e = f.emergency(f.perceive(), self.alarm0) or (kind, None)
            note += ' -> ' + f.handle(*e)
        self.t += 1

        s = self.game.snapshot()
        info = {'option': name, 'result': note, 'room': s['room'] if s else None}
        if s:
            for e in self.brief.update(s):
                if e.startswith('objective DONE'):
                    r += 50.0                            # any objective, main or side (crate scans, files)
            self._checkpoint(s)
            used = f.interact_objectives()
            if used:
                info['result'] = note = note + ' +' + used
        if s and self.prev and self.prev['sam'][0][2] - s['sam'][0][2] > 400:
            f.log('fell %.0f m at %s: episode over' % ((self.prev['sam'][0][2] - s['sam'][0][2]) / 100,
                                                      [round(v) for v in self.prev['sam'][0]]))
            self.prev = s
            return self._obs(s, self.goal), r - 20.0, False, True, dict(info, end='fell')
        if note == 'all_exits_dead':                        # nothing left to try from here: next episode
            self.prev = s or self.prev
            return self._obs(self.prev, self.goal), r, False, True, dict(info, end='exhausted')
        if s is None or s['health'] <= 0 or s['mission'] in (None, 'menu'):
            return self._obs(self.prev, self.goal), -100.0, True, False, dict(info, end='dead')
        if ('abort' in note or s['alarm'] > self.alarm0) and not getattr(self, 'map_mode', False):
            self.prev = s
            return self._obs(s, self.goal), r - 100.0, True, False, dict(info, end='detected')
        if f.audit_bodies(f.perceive()) == 'body_in_view':
            r -= 30.0
        done_now = {o[0] for o in s['objs'] if o[1] == 1}
        if done_now - self.done_objs:
            f.save()
            return self._obs(s, self.goal), r + 100.0, True, False, dict(info, end='objective')
        new_goal = self._goal(s)
        if new_goal and new_goal[0]['objective'] == self.goal[0]['objective']:
            if len(new_goal[1]) < self.best_rooms:   # closest room to the goal so far: paid once
                self.best_rooms = len(new_goal[1])
                r += 10.0
            self.goal = new_goal
            phi = self._potential(s, self.goal)
            r += phi - self.phi
            self.phi = phi
        elif new_goal:
            self.goal, self.phi = new_goal, self._potential(s, new_goal)
        susp = self._suspicious(s)
        if susp > self.susp:
            r -= 5.0
        self.susp = susp
        self.prev = s
        truncated = self.t >= self.max_steps
        if truncated:
            f.save()
        return self._obs(s, self.goal), r, False, truncated, info


def baseline_policy(env):
    """The playbook alone, no learning: what the player would do. Used as the score to beat."""
    f, s = env.fisher, env.fisher.perceive()
    if env._bodies(s):
        return OPTIONS.index('DUMP')
    t = f.takedown_target(s, max_m=8)
    if t and env.goal and flat(t['g']['loc'], env.goal[2][0]) < flat(s['sam'][0], env.goal[2][0]):
        return OPTIONS.index('TAKEDOWN')                    # a guard between us and the goal
    return OPTIONS.index('ADVANCE')
