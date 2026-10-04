"""Sam controller: scancode keys + relative mouse into the game, game memory for eyes. Every move is recorded
(runs/, player 'bot') by the training-set recorder.

  python sam.py calibrate                    mouse counts per degree of camera yaw
  python sam.py look                         where Sam is, facing, nearest guards, rooms
  python sam.py turn DEG                     turn the camera by DEG (+ = right)
  python sam.py face X Y                     face a world point
  python sam.py walk M [crouch]              walk forward M metres (steering straight), safety reflex on
  python sam.py goto X Y [crouch]            walk to a world point, steering, safety reflex on
  python sam.py key NAME [hold_s]            tap/hold a bound key: crouch jump use whistle fire alt
"""
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import sys
import time

sys.path.insert(0, r'C:\Program Files (x86)\Steam\steamapps\common\Splintercell Chaos Theory\CheatOverlay')
ctypes.windll.shcore.SetProcessDpiAwareness(2)
import cheat_overlay as co

u32 = ctypes.windll.user32
HERE = os.path.dirname(os.path.abspath(__file__))
CAL = os.path.join(HERE, 'sam_cal.json')
SC = {'w': 0x11, 'a': 0x1E, 's': 0x1F, 'd': 0x20, 'crouch': 0x2E, 'jump': 0x2A, 'use': 0x39, 'whistle': 0x2F,
      'reload': 0x13}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT)]
    _anonymous_ = ('u',)
    _fields_ = [('type', wt.DWORD), ('u', _U)]


def key(name, up):
    i = INPUT(type=1)
    i.ki = KEYBDINPUT(0, SC[name], 0x0008 | (0x0002 if up else 0), 0, 0)
    u32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def tap(name, hold=0.08):
    key(name, False)
    time.sleep(hold)
    key(name, True)


def wheel(ticks):
    """Mouse wheel: movement speed in this game (up = faster, down = slower)."""
    i = INPUT(type=0)
    i.mi = MOUSEINPUT(0, 0, (120 * int(ticks)) & 0xFFFFFFFF, 0x0800, 0, 0)
    u32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def mouse(dx, dy=0):
    i = INPUT(type=0)
    i.mi = MOUSEINPUT(int(dx), int(dy), 0, 0x0001, 0, 0)
    u32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


class Sam:
    def __init__(self):
        pid = co.find_pid()
        self.g = co.Game(co.Mem(pid))
        self.g.mission_state()
        self.h = co.game_window(pid)
        self.cal = json.load(open(CAL)) if os.path.exists(CAL) else None
        self.log = type('NoLog', (), {'tick': lambda *a, **k: None, 'close': lambda *a: None})()  # OPSAT records runs
        self.heartbeat()

    def heartbeat(self):
        """Tell OPSAT's run recorder the bot is driving (samples get "b":1)."""
        flag = os.path.join(os.path.expanduser('~'), 'Saved Games', 'OPSAT', 'runs', 'bot_active')
        try:
            os.makedirs(os.path.dirname(flag), exist_ok=True)
            open(flag, 'a').close()
            os.utime(flag)
        except OSError:
            pass

    def front(self):
        if u32.GetForegroundWindow() != self.h:
            co.force_foreground(self.h)
            time.sleep(0.4)

    def intel(self):
        self.heartbeat()
        intel = self.g.intel()
        mission, room, objs = self.g.mission_state()
        self.log.tick(mission, room, objs, intel, co.guard_mood, co.relative)
        self.room = room
        return intel

    def pawn(self):
        return self.g.m.u32(self.g.players[0] + self.g.pawn_off)

    def crouched(self):
        return bool(self.g.m.u32(self.pawn() + 696) & 2)        # Pawn.bIsCrouched

    def light(self):
        return self.g._f32(self.pawn() + 612)                    # Actor.LuminosityFactor (light meter input)

    def crouch(self, on=True):
        for _ in range(4):
            if self.crouched() == on:
                return True
            tap('crouch', 0.15)
            time.sleep(0.45)
        return self.crouched() == on

    def pose(self):
        (x, y, z), yaw = self.intel()['sam']
        return x, y, z, yaw / 65536 * 360

    def turn(self, deg):
        """Relative camera turn in degrees (+ right), closed loop on the yaw read from memory."""
        target = (self.pose()[3] + deg) % 360
        for _ in range(12):
            err = (target - self.pose()[3] + 180) % 360 - 180
            if abs(err) < 1.5:
                return
            step = max(-60, min(60, err))
            mouse(step * self.cal['counts_per_deg'])
            time.sleep(0.06)

    def face(self, x, y):
        sx, sy, _, yaw = self.pose()
        heading = math.degrees(math.atan2(y - sy, x - sx)) % 360
        self.turn((heading - yaw + 180) % 360 - 180)

    def danger(self, intel):
        """A conscious guard on Sam's level, within 12 m, whose view cone holds Sam (geometry only)."""
        sam = intel['sam']
        for g in intel.get('guards', []):
            mood = co.guard_mood(g)[0]
            if mood in ('DEAD', 'OUT'):
                continue
            d, _, dz = co.relative(sam, g['loc'])
            if d < 12 and abs(dz) < 2.5 and co.facing(g['loc'], g['yaw'], g['cone'] or 60, 1200, sam[0]):
                return '%s guard %.0fm facing Sam' % (mood.lower(), d)
        return None

    DARK = 10.0        # LuminosityFactor: ~4 crouched in shadow, ~67 in front of the lit bank

    def compromised(self, intel):
        """Run is blown: an alarm, or a guard within 40 m gone alert. (User rule: then Esc and reload.)"""
        if intel.get('alarm'):
            return 'alarm %s' % intel['alarm']
        for g in intel.get('guards', []):
            if co.guard_mood(g)[0] == 'ALERT' and co.relative(intel['sam'], g['loc'])[0] < 40:
                return 'guard alert at %.0fm' % co.relative(intel['sam'], g['loc'])[0]
        return None

    def watched(self, intel, radius=15.0):
        """Nearest conscious guard on Sam's level within radius whose view cone holds Sam, or None."""
        sam, best = intel['sam'], None
        for g in intel.get('guards', []):
            if co.guard_mood(g)[0] in ('DEAD', 'OUT'):
                continue
            d, _, dz = co.relative(sam, g['loc'])
            if d < radius and abs(dz) < 2.5 and co.facing(g['loc'], g['yaw'], max(g['cone'] or 60, 70), 4000, sam[0]):
                best = d if best is None else min(best, d)
        return best

    def goto(self, x, y, crouch=True, tol=0.6, timeout=40.0, trail=None, light_limit=20.0):
        """Steer to a world point holding W. Watched while lit -> back off to the last dark spot of the trail;
        watched in shadow -> hold still; run blown -> return 'compromised'. Crouch is re-checked as it goes."""
        if crouch is not None:
            self.crouch(crouch)
        trail = trail if trail is not None else []
        t0, holding, best, best_t, last_crouch = time.monotonic(), False, 1e9, time.monotonic(), 0.0
        result = 'timeout'
        try:
            while time.monotonic() - t0 < timeout:
                intel = self.intel()
                blown = self.compromised(intel)
                if blown:
                    result = 'compromised: ' + blown
                    break
                (sx, sy, _), yaw = intel['sam']
                light = self.light()
                if light < self.DARK:
                    trail.append((sx, sy))
                elif light > light_limit and trail:  # walking into light: back to the last dark spot and stop
                    key('w', True)
                    holding = False
                    bx, by = trail[-1]
                    self.goto(bx, by, crouch, tol=0.5, timeout=8, trail=trail[:-1], light_limit=1e9)
                    result = 'lit %.0f ahead at (%.0f, %.0f)' % (light, sx, sy)
                    break
                dist = math.hypot(x - sx, y - sy) / 100
                if dist < tol:
                    result = 'arrived'
                    break
                seen = self.watched(intel)
                if seen is not None:
                    if holding:
                        key('w', True)
                        holding = False
                    if light >= self.DARK and trail:
                        bx, by = trail[-1]
                        print('  lit (%.0f) and watched by a guard %.0fm away: back to the dark at (%.0f, %.0f)' % (
                            light, seen, bx, by))
                        r = self.goto(bx, by, crouch, tol=0.5, timeout=8, trail=trail[:-1])
                        if r.startswith('compromised'):
                            result = r
                            break
                    else:
                        print('  in shadow (%.0f), guard %.0fm looking this way: hold' % (light, seen))
                        time.sleep(0.3)
                    best_t = time.monotonic()
                    continue
                if crouch and time.monotonic() - last_crouch > 1.0:  # stairs, ledges and doors can stand Sam up
                    last_crouch = time.monotonic()
                    if not self.crouched():
                        key('w', True)
                        holding = False
                        self.crouch(True)
                err = (math.degrees(math.atan2(y - sy, x - sx)) - yaw / 65536 * 360 + 180) % 360 - 180
                mouse(max(-25, min(25, err * 0.5)) * self.cal['counts_per_deg'])
                if abs(err) > 45 and holding:  # facing away: turn on the spot first
                    key('w', True)
                    holding = False
                elif abs(err) <= 45 and not holding:
                    key('w', False)
                    holding = True
                if dist < best - 0.15:
                    best, best_t = dist, time.monotonic()
                elif time.monotonic() - best_t > 2.5:
                    result = 'stuck %.1fm short' % dist
                    break
                time.sleep(0.05)
        finally:
            key('w', True)
        sx, sy, sz, yaw = self.pose()
        print('goto ->', result, '| at (%.0f, %.0f, %.0f) yaw %.0f room %s | crouched %s light %.1f' % (
            sx, sy, sz, yaw, self.room, self.crouched(), self.light()))
        return result

    def route(self, points, **kw):
        """Waypoints in order; stops at the first that does not arrive."""
        trail = []
        for x, y in points:
            r = self.goto(x, y, trail=trail, **kw)
            if r != 'arrived':
                return r
        return 'arrived'

    def menu_keys(self, *names):
        codes = {'w': 0x11, 's': 0x1F, 'enter': 0x1C, 'esc': 0x01}
        for n in names:
            for up in (False, True):
                i = INPUT(type=1)
                i.ki = KEYBDINPUT(0, codes[n], 0x0008 | (0x0002 if up else 0), 0, 0)
                u32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))
                time.sleep(0.08)
            time.sleep(0.35)

    def reset(self):
        """User rule: when the run is blown, Esc and reload. Loads the newest save (my last checkpoint)."""
        self.front()
        for k in ('w', 'a', 's', 'd'):
            key(k, True)
        self.menu_keys('esc')
        time.sleep(0.8)
        self.menu_keys(*['w'] * 7)           # clamp to SAVE GAME
        self.menu_keys('s', 'enter')         # LOAD GAME
        time.sleep(1.2)
        self.menu_keys(*['w'] * 25)          # top of the list = newest save
        self.menu_keys('enter')
        time.sleep(0.8)
        self.menu_keys('w', 'enter')         # YES
        time.sleep(14)
        print('reset: reloaded the newest save')

    def walk(self, metres, crouch=True):
        sx, sy, _, yaw = self.pose()
        a = math.radians(yaw)
        return self.goto(sx + math.cos(a) * metres * 100, sy + math.sin(a) * metres * 100, crouch)

    def look(self):
        intel = self.intel()
        sam = intel['sam']
        print('room %s | Sam (%.0f, %.0f, %.0f) facing %.0f deg | alarm %s | crouched %s light %.2f' % (
            self.room, *sam[0], sam[1] / 65536 * 360, intel.get('alarm'), self.crouched(), self.light()))
        for gd in sorted(intel['guards'], key=lambda g: co.relative(sam, g['loc'])[0])[:6]:
            d, b, dz = co.relative(sam, gd['loc'])
            print('  guard %4.1fm at %3.0f deg (clock %d) dz%+.1f %-10s %s facing-Sam=%s' % (
                d, b, int(round(b / 30)) % 12 or 12, dz, co.guard_mood(gd)[0], gd['goal'],
                co.facing(gd['loc'], gd['yaw'], gd['cone'] or 60, 1500, sam[0])))
        print('  danger:', self.danger(intel))

    def calibrate(self):
        self.front()
        y0 = self.pose()[3]
        mouse(300)
        time.sleep(0.5)
        y1 = self.pose()[3]
        mouse(-300)
        time.sleep(0.5)
        d = (y1 - y0 + 180) % 360 - 180
        self.cal = {'counts_per_deg': 300 / d if d else 10}
        json.dump(self.cal, open(CAL, 'w'))
        print('300 counts -> %.2f deg; %.2f counts/deg' % (d, self.cal['counts_per_deg']))


if __name__ == '__main__':
    s, cmd, a = Sam(), sys.argv[1], sys.argv[2:]
    if cmd != 'look':
        s.front()
    if cmd == 'calibrate':
        s.calibrate()
    elif cmd == 'look':
        s.look()
    elif cmd == 'turn':
        s.turn(float(a[0]))
        s.look()
    elif cmd == 'face':
        s.face(float(a[0]), float(a[1]))
    elif cmd == 'walk':
        s.walk(float(a[0]), 'stand' not in a)
    elif cmd == 'goto':
        s.goto(float(a[0]), float(a[1]), 'stand' not in a)
    elif cmd == 'speed':
        wheel(int(a[0]))
    elif cmd == 'reset':
        s.reset()
    elif cmd == 'route':
        pts = [tuple(map(float, p.split(','))) for p in a]
        r = s.route(pts)
        print('route ->', r)
    elif cmd == 'key':
        tap(a[0], float(a[1]) if len(a) > 1 else 0.08)
    s.log.close()
