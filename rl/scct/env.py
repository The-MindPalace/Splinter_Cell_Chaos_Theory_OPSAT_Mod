"""SCCTNav-v0: the smallest Splinter Cell Chaos Theory environment - navigate to the next objective unseen.

Self-play only: the agent gets the game's own goal (the next pending objective beacon on the 3D map, routed
through the map's room graph) and learns everything else - movement, light, guards - from reward. No human
routes or demonstrations go in; the recorded runs are only used to evaluate.

Real time, 4 agent steps per second (each action is held for STEP_S). Reset = F8 quickload of the stage's
quicksave (made with F5 the first time a stage starts).
"""
import math
import time

import numpy as np
import gymnasium as gym
from gymnasium import spaces

from .game import Game, guard_view, tap, co

STEP_S = 0.25
MOVES = [set(), {'w'}, {'s'}, {'a'}, {'d'}, {'w', 'a'}, {'w', 'd'}]          # 7
TURNS = [-30.0, -10.0, 0.0, 10.0, 30.0]                                     # 5 (degrees per step)
N_GUARDS = 4
OBS_DIM = 14 + N_GUARDS * 5


class SCCTNavEnv(gym.Env):
    """Observation (float32, OBS_DIM):
        goal in camera frame: right, forward, up (m, /50), distance (/100), goal heading sin/cos
        route rooms left (/10), light (/50), crouched, last-step speed (m/s /5), blocked flag,
        alarm stage (/4), health (/100), time left in episode (0..1)
        4 nearest guards: right, forward, up (/30), facing Sam, mood (0 calm .5 suspicious 1 alert)
    Action: MultiDiscrete([7 move, 5 turn, 2 crouch toggle, 3 none/jump/interact])
    Reward: +1 per metre of route progress (potential-based), +10 per route room entered, +100 objective
        completed, -0.01 per step, -0.02 x light while a guard within 15 m faces Sam, -5 when a guard turns
        suspicious; terminal -100 detected (alert/alarm), -100 dead.
    Terminated: objective completed (success), detected, dead. Truncated: max_steps, or stuck 40 steps.
    """
    metadata = {'render_modes': []}

    def __init__(self, max_steps=1200, reset_mode='quickload', detect_ends=True, pixels=True, full_actions=False):
        """pixels: add an 84x84 grayscale view of the game (Dict observation {'vec', 'img'}) so the policy can
        see walls and openings. full_actions: include jump/interact (off for navigation: no hopping around)."""
        super().__init__()
        vec = spaces.Box(-np.inf, np.inf, (OBS_DIM,), np.float32)
        self.pixels = pixels
        self.observation_space = spaces.Dict({'vec': vec, 'img': spaces.Box(0, 255, (84, 84, 1), np.uint8)})             if pixels else vec
        self.full_actions = full_actions
        self.action_space = spaces.MultiDiscrete([len(MOVES), len(TURNS), 2] + ([3] if full_actions else []))
        self.max_steps, self.reset_mode, self.detect_ends = max_steps, reset_mode, detect_ends
        self.unattended = False   # True: bring the game back to the front when something steals focus
        self.game = Game()
        self.has_quicksave = False

    # --- goal: next pending objective, routed through the 3D map's room graph -----------------------
    def _goal(self, s):
        ids = {o[2]: (o[1], o[4]) for o in s['objs']}
        given = [m for m in s['markers'] if ids.get(m['objective'], (None,))[0] == 0]
        if not given:
            return None
        order = {o[2]: i for i, o in enumerate(s['objs'])}
        sam = s['sam']
        m = min(given, key=lambda m: (ids[m['objective']][1] not in (0, 3), order.get(m['objective'], 99),
                                     co.relative(sam, m['loc'])[0]))
        route = self.game.g.route(s['room'], m.get('room')) or [s['room']]
        # waypoints: centres of the rooms still to cross, then the beacon itself
        pts = [s['rooms'][r][:3] for r in route[1:] if r in s['rooms']] + [m['loc']]
        return m, route, pts

    def _potential(self, s, goal):
        """Remaining route length in metres (negative potential)."""
        _, _, pts = goal
        (x, y, z), _ = s['sam']
        d, px, py = 0.0, x, y
        for wx, wy, _ in pts:
            d += math.hypot(wx - px, wy - py) / 100
            px, py = wx, wy
        return -d

    # --- observation -----------------------------------------------------------------------------
    def _obs(self, s, goal):
        v = self._vec(s, goal)
        return {'vec': v, 'img': self.game.frame()} if self.pixels else v

    def _vec(self, s, goal):
        (x, y, z), yaw = s['sam']
        a = yaw / 65536 * 2 * math.pi
        wx, wy, wz = goal[2][0]
        dx, dy = wx - x, wy - y
        fwd = (dx * math.cos(a) + dy * math.sin(a)) / 100
        right = (-dx * math.sin(a) + dy * math.cos(a)) / 100
        dist = math.hypot(dx, dy) / 100
        head = math.atan2(right, fwd)
        g = guard_view(s['sam'], s['intel'].get('guards', []), N_GUARDS)
        g += [(0.0, 0.0, 0.0, 0.0, 0.0)] * (N_GUARDS - len(g))
        v = [right / 50, fwd / 50, (wz - z) / 100 / 50, dist / 100, math.sin(head), math.cos(head),
             (len(goal[1]) - 1) / 10, min(s['light'], 100) / 50, float(s['crouched']), self.speed / 5,
             float(self.blocked), s['alarm'] / 4, min(s['health'], 200) / 100, 1 - self.t / self.max_steps]
        for gr, gf, gz, sees, mood in g:
            v += [gr / 30, gf / 30, gz / 30, sees, mood]
        return np.asarray(v, np.float32)

    # --- episode ---------------------------------------------------------------------------------
    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        game = self.game
        game.release_all()
        if not co.find_pid():
            raise RuntimeError('game closed - training stopped')
        self._guard_focus()  # waits for you to bring the game back; never steals focus
        if self.has_quicksave and self.reset_mode == 'quickload':
            tap('quickload', 0.1)
            self._wait_live(20)
        else:  # first episode: this spot becomes the stage start
            self._wait_live(20)
            game.set_crouch(True)
            tap('quicksave', 0.1)
            time.sleep(1.5)
            self.has_quicksave = True
        s = game.snapshot()
        self.goal = self._goal(s)
        if not self.goal:
            raise RuntimeError('no pending objective with a map beacon')
        self.t, self.speed, self.blocked, self.still = 0, 0.0, False, 0
        self.prev, self.phi = s, self._potential(s, self.goal)
        self.best_rooms = len(self.goal[1])
        self.done_objs = {o[0] for o in s['objs'] if o[1] == 1}
        self.susp = self._suspicious(s)
        return self._obs(s, self.goal), {'room': s['room'], 'goal_room': self.goal[0].get('room')}

    def _wait_live(self, timeout):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            try:
                s = self.game.snapshot()
                if s and s['mission'] not in (None, 'menu') and s['objs'] and s['health'] > 0:
                    time.sleep(0.5)
                    return s
            except Exception:
                pass
            time.sleep(0.5)
        raise RuntimeError('game did not come back after reset')

    def _suspicious(self, s):
        return sum(1 for g in s['intel'].get('guards', []) if co.guard_mood(g)[0] == 'SUSPICIOUS'
                   and co.relative(s['sam'], g['loc'])[0] < 30)

    def _guard_focus(self):
        """Never type into another window: if the game is not in front (alt-tab, closed), release every key and
        wait until it is back. Raises if the game is gone."""
        game = self.game
        if game.in_front():
            return
        game.release_all()
        t0 = time.monotonic()
        while not game.in_front():
            if not co.find_pid():
                raise RuntimeError('game closed - training stopped')
            if self.unattended and time.monotonic() - t0 > 3:   # nobody at the PC: take the game back
                game.focus()
            time.sleep(0.5)
        time.sleep(0.5)

    def step(self, action):
        a = [int(x) for x in action]
        move, turn, crouch, act = a[0], a[1], a[2], (a[3] if self.full_actions else 0)
        game = self.game
        self._guard_focus()
        game.hold(MOVES[move])
        if TURNS[turn]:                       # glide, never snap: 3-degree steps
            n = max(1, int(abs(TURNS[turn]) / 3))
            for _ in range(n):
                game.turn(TURNS[turn] / n)
                time.sleep(0.012)
        if crouch:
            tap('crouch', 0.06)
        if act == 1:
            tap('jump', 0.06)
        elif act == 2:
            tap('use', 0.06)
        time.sleep(STEP_S)
        if not game.in_front():  # focus left mid-step: let go at once
            game.release_all()
        self.t += 1
        s = game.snapshot()
        info = {'room': s['room'] if s else None}
        if s is None or s['health'] <= 0 or s['mission'] in (None, 'menu'):
            game.release_all()
            return self._obs(self.prev, self.goal), -100.0, True, False, dict(info, end='dead')

        (x, y, _), _ = s['sam']
        (px, py, _), _ = self.prev['sam']
        moved = math.hypot(x - px, y - py) / 100
        self.speed = moved / STEP_S
        self.blocked = bool(MOVES[move]) and moved < 0.05
        self.still = self.still + 1 if self.blocked else 0

        r = -0.01
        done_now = {o[0] for o in s['objs'] if o[1] == 1}
        if done_now - self.done_objs:                       # objective completed: episode success
            game.release_all()
            return self._obs(s, self.goal), r + 100.0, True, False, dict(info, end='objective')
        new_goal = self._goal(s)
        if new_goal:
            if new_goal[0]['objective'] != self.goal[0]['objective']:
                self.goal, self.phi = new_goal, self._potential(s, new_goal)
            else:
                if len(new_goal[1]) < self.best_rooms:   # closest room to the goal so far: paid once
                    self.best_rooms = len(new_goal[1])
                    r += 10.0                               # entered the next room on the route
                self.goal = new_goal
                phi = self._potential(s, self.goal)
                r += phi - self.phi                         # metres of route progress
                self.phi = phi
        watched = any(gv[3] and math.hypot(gv[0], gv[1]) < 15
                      for gv in guard_view(s['sam'], s['intel'].get('guards', []), N_GUARDS))
        if watched:
            r -= 0.02 * min(s['light'], 100)
        susp = self._suspicious(s)
        if susp > self.susp:
            r -= 5.0
        self.susp = susp

        detected = s['alarm'] > self.prev['alarm'] or any(
            co.guard_mood(g)[0] == 'ALERT' and co.relative(s['sam'], g['loc'])[0] < 40
            for g in s['intel'].get('guards', []))
        self.prev = s
        obs = self._obs(s, self.goal)
        if detected and self.detect_ends:
            game.release_all()
            return obs, r - 100.0, True, False, dict(info, end='detected')
        truncated = self.t >= self.max_steps or self.still >= 40
        if truncated:
            game.release_all()
            info['end'] = 'stuck' if self.still >= 40 else 'time'
        return obs, r, False, truncated, info

    def close(self):
        self.game.release_all()
