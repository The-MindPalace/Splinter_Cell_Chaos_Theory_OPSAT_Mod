"""Live session helper (read-only memory + OPSAT hotkeys).

  python live.py state            game memory: mission, room, Sam, guards (mood inputs), markers
  python live.py keys up right    bring the game forward, tap keys (up/down/left/right/ins/esc)
  python live.py shot NAME        screenshot (OPSAT included) -> live/NAME.png (+ _panel crop)
"""
import ctypes
import json
import math
import os
import sys
import time

sys.path.insert(0, r'C:\Program Files (x86)\Steam\steamapps\common\Splintercell Chaos Theory\CheatOverlay')
ctypes.windll.shcore.SetProcessDpiAwareness(2)
import cheat_overlay as co

u32 = ctypes.windll.user32
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live')
os.makedirs(OUT, exist_ok=True)
VK = {'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27, 'ins': 0x2D, 'esc': 0x1B, 'enter': 0x0D}


def game_hwnd():
    pid = co.find_pid()
    return pid, (co.game_window(pid) if pid else None)


def front():
    pid, h = game_hwnd()
    if h and u32.GetForegroundWindow() != h:
        co.force_foreground(h)
        time.sleep(0.4)
    return h and u32.GetForegroundWindow() == h


def keys(names):
    print('game in front:', front())
    for n in names:
        if n.startswith('wait'):
            time.sleep(float(n[4:] or 1))
            continue
        vk = VK[n]
        u32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.09)
        u32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.5)


def shot(name):
    from PIL import ImageGrab
    time.sleep(0.2)
    im = ImageGrab.grab(include_layered_windows=True)
    im.save(os.path.join(OUT, name + '.png'))
    W, H = im.size
    im.crop((0, int(H * 0.12), int(W * 0.40), H)).save(os.path.join(OUT, name + '_panel.png'))
    print('saved', name, im.size)


def state():
    pid, h = game_hwnd()
    g = co.Game(co.Mem(pid))
    mission, room, objs = g.mission_state()
    intel = g.intel() or {}
    sam = intel.get('sam')
    print('mission', mission, '| room', room, '| fg', u32.GetForegroundWindow() == h, '| alarm', intel.get('alarm'))
    print('objs', [(o[0], o[1], o[2], o[4]) for o in objs])
    print('sam', [round(v) for v in sam[0]], 'yaw', sam[1])
    rows = []
    for gd in intel.get('guards', []):
        d, b, dz = co.relative(sam, gd['loc'])
        rows.append((d, '%5.1fm %3.0fdeg dz%+5.1f %-10s stress=%-14s event=%-22s goal=%-16s state=%s cone=%.0f' % (
            d, b, dz, co.guard_mood(gd)[0], gd['stress'], gd['event'], gd['goal'], gd['state'], gd['cone'] or 0)))
    for _, r in sorted(rows)[:14]:
        print('  guard', r)
    for m in intel.get('objectives') or []:
        d, b, dz = co.relative(sam, m['loc'])
        print('  marker %-12s %-14s room=%-16s approx=%s done=%s %.0fm %3.0fdeg dz%+.1f' % (
            m['objective'], m['name'], m['room'], m.get('approx'), m['done'], d, b, dz))
    print('sensors', len(intel.get('sensors', [])), [(s['name'], s['state'], round(co.relative(sam, s['loc'])[0])) for s in intel.get('sensors', [])][:8])


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'state':
        state()
    elif cmd == 'keys':
        keys(sys.argv[2:])
    elif cmd == 'shot':
        shot(sys.argv[2])
