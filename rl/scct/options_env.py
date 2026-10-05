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

from .env import SCCTNavEnv
from .skills import Fisher, KO, flat

OPTIONS = ['ADVANCE', 'WAIT', 'HIDE', 'TAKEDOWN', 'DUMP', 'SIDESTEP_L', 'SIDESTEP_R']


class SCCTFisherEnv(SCCTNavEnv):
    def __init__(self, max_decisions=300, pixels=True, **kw):
        super().__init__(max_steps=max_decisions, pixels=pixels, **kw)
        self.action_space = spaces.Discrete(len(OPTIONS))
        self.fisher = Fisher(self.game, log=lambda *a: print('[fisher]', *a))
        self.dead_exits = set()     # (room, next room) pairs exploration proved unreachable

    def reset(self, seed=None, options=None):
        obs, info = super().reset(seed=seed, options=options)
        self.fisher.carrying, self.fisher.bodies = None, []
        self.fisher.save()          # keep what the last episode learned (walkable ground, dark spots)
        self.dumped = set()
        self.alarm0 = self.prev['alarm']
        return obs, info

    def _next_room(self, route):
        """The next room to enter: the route's next room unless exploration proved it unreachable from here;
        then the adjacent room with the shortest remaining route to the objective."""
        here, target_room = route[0], route[-1]
        if (here, route[1]) not in self.dead_exits:
            return route[1]
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
        if name == 'ADVANCE':
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
        if s is None or s['health'] <= 0 or s['mission'] in (None, 'menu'):
            return self._obs(self.prev, self.goal), -100.0, True, False, dict(info, end='dead')
        if 'abort' in note or s['alarm'] > self.alarm0:
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
