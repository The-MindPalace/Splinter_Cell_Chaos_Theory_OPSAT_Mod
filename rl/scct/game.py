"""Game I/O for the RL environment: telemetry from game memory (OPSAT's reader) and inputs into the game.

Inputs must be scancode SendInput (the game ignores virtual-key events); the camera turns with relative
mouse moves. The game window has to be in front on an unlocked desktop - Windows blocks synthetic input and
screen capture otherwise.
"""
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import sys
import time

import numpy as np

GAME_DIR = r'C:\Program Files (x86)\Steam\steamapps\common\Splintercell Chaos Theory\CheatOverlay'
sys.path.insert(0, GAME_DIR)
import cheat_overlay as co  # noqa: E402  (OPSAT's memory reader)

u32 = ctypes.windll.user32
HERE = os.path.dirname(os.path.abspath(__file__))
CAL = os.path.join(HERE, '..', '..', 'tools', 'bot', 'sam_cal.json')

# scancodes for the DEFAULT profile bindings
SC = {'w': 0x11, 'a': 0x1E, 's': 0x1F, 'd': 0x20, 'crouch': 0x2E, 'jump': 0x2A, 'use': 0x39,
      'quicksave': 0x3F, 'quickload': 0x42, 'enter': 0x1C, 'esc': 0x01}   # F5 = 0x3F, F8 = 0x42
PAWN_CROUCHED, PAWN_LIGHT = (696, 2), 612          # Pawn.bIsCrouched bit, Actor.LuminosityFactor


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


def _send(inp):
    u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def key(name, up):
    i = INPUT(type=1)
    i.ki = KEYBDINPUT(0, SC[name], 0x0008 | (0x0002 if up else 0), 0, 0)
    _send(i)


def tap(name, hold=0.08):
    key(name, False)
    time.sleep(hold)
    key(name, True)


def mouse(dx, dy=0):
    i = INPUT(type=0)
    i.mi = MOUSEINPUT(int(dx), int(dy), 0, 0x0001, 0, 0)
    _send(i)


class Game:
    """One attached game. snapshot() is everything the env needs from one memory read."""

    def __init__(self):
        pid = co.find_pid()
        if not pid:
            raise RuntimeError('Splinter Cell Chaos Theory is not running')
        self.pid = pid
        self.g = co.Game(co.Mem(pid))
        self.g.mission_state()
        self.hwnd = co.game_window(pid)
        self.counts_per_deg = json.load(open(CAL))['counts_per_deg'] if os.path.exists(CAL) else 15.3
        self.held = set()

    # --- telemetry ---------------------------------------------------------------------------------
    def snapshot(self):
        mission, room, objs = self.g.mission_state()
        intel = self.g.intel()
        if not intel:
            return None
        pawn = self.g.m.u32(self.g.players[0] + self.g.pawn_off)
        off, bit = PAWN_CROUCHED
        health_off = self.g.props[('Pawn', 'Health')][0]
        return {
            'mission': mission, 'room': room, 'objs': objs, 'intel': intel,
            'sam': intel['sam'], 'alarm': intel.get('alarm') or 0,
            'crouched': bool(self.g.m.u32(pawn + off) & bit),
            'light': self.g._f32(pawn + PAWN_LIGHT),
            'health': self.g.m.u32(pawn + health_off) if pawn else 0,
            'graph': getattr(self.g, 'room_graph', {}), 'rooms': getattr(self.g, 'room_world', {}),
            'markers': intel.get('objectives') or [],
        }

    def frame(self, size=84):
        """What Sam sees: the game window as a size x size grayscale image (uint8, HxWx1). The default grab
        leaves out layered windows, so OPSAT's overlay is not in the picture."""
        from PIL import ImageGrab
        r = wt.RECT()
        u32.GetWindowRect(self.hwnd, ctypes.byref(r))
        img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom)).convert('L').resize((size, size))
        return np.asarray(img, np.uint8)[:, :, None]

    # --- input -----------------------------------------------------------------------------------
    def in_front(self):
        return bool(self.hwnd) and u32.GetForegroundWindow() == self.hwnd

    def focus(self):
        if not self.in_front():
            co.force_foreground(self.hwnd)
            time.sleep(0.4)
        return self.in_front()

    def hold(self, keys):
        """Hold exactly this set of movement keys (releases the rest)."""
        for k in self.held - keys:
            key(k, True)
        for k in keys - self.held:
            key(k, False)
        self.held = set(keys)

    def release_all(self):
        self.hold(set())

    def turn(self, deg):
        mouse(deg * self.counts_per_deg)

    def set_crouch(self, on):
        for _ in range(3):
            if self.snapshot()['crouched'] == on:
                return
            tap('crouch', 0.12)
            time.sleep(0.45)


def guard_view(sam, guards, n=4, radius=30.0):
    """The n nearest conscious guards in Sam's camera frame: (right m, forward m, dz m, facing Sam, mood)."""
    (sx, sy, sz), yaw = sam
    a = yaw / 65536 * 2 * math.pi
    out = []
    for g in guards:
        mood = co.guard_mood(g)[0]
        if mood in ('DEAD', 'OUT'):
            continue
        dx, dy = g['loc'][0] - sx, g['loc'][1] - sy
        d = math.hypot(dx, dy) / 100
        if d > radius:
            continue
        fwd = (dx * math.cos(a) + dy * math.sin(a)) / 100
        right = (-dx * math.sin(a) + dy * math.cos(a)) / 100
        sees = co.facing(g['loc'], g['yaw'], max(g.get('cone') or 60, 60), 3000, sam[0])
        out.append((d, right, fwd, (g['loc'][2] - sz) / 100, float(sees),
                    {'CALM': 0.0, 'SUSPICIOUS': 0.5, 'ALERT': 1.0}.get(mood, 0.0)))
    out.sort()
    return [o[1:] for o in out[:n]]
