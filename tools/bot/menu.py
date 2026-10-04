"""Menu helper: python menu.py X Y -> move the game cursor to (X, Y) by relative steps (closed loop) and click."""
import subprocess, sys, time, re
exec(open('sam.py', encoding='utf-8').read().split("if __name__ == '__main__':")[0])
import cheat_overlay as co
from PIL import ImageGrab
h = co.game_window(co.find_pid())
if u32.GetForegroundWindow() != h:
    co.force_foreground(h); time.sleep(0.4)
PX_PER_COUNT = 4.5


def find():
    im = ImageGrab.grab(); px = im.load(); best = None
    for y in range(100, 1040, 2):
        for x in range(250, 1670, 2):
            r, g, b = px[x, y][:3]
            if g > 200 and r > 170 and b > 150 and abs(r - b) < 60:
                if best is None or y < best[1] - 6:
                    best = (x, y)
    return best


tx, ty = int(sys.argv[1]), int(sys.argv[2])
for _ in range(4):
    c = find()
    if not c:
        print('cursor not found'); break
    dx, dy = (tx - c[0]) / PX_PER_COUNT, (ty - c[1]) / PX_PER_COUNT
    if abs(dx) < 2 and abs(dy) < 2:
        break
    n = max(1, int(max(abs(dx), abs(dy)) // 10))
    for _ in range(n):
        mouse(dx / n, dy / n); time.sleep(0.015)
    time.sleep(0.3)
print('cursor at', find())
if 'noclick' not in sys.argv:
    for flag in (0x0002, 0x0004):
        i = INPUT(type=0); i.mi = MOUSEINPUT(0, 0, 0, flag, 0, 0)
        u32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT)); time.sleep(0.1)
    print('clicked')
