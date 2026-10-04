"""Screenshot + memory at the same instant; draw where OPSAT's radar should put each guard / marker (red rings)."""
import ctypes
import math
import os
import sys
import time

sys.path.insert(0, r'C:\Program Files (x86)\Steam\steamapps\common\Splintercell Chaos Theory\CheatOverlay')
ctypes.windll.shcore.SetProcessDpiAwareness(2)
import cheat_overlay as co
import hud
from PIL import ImageGrab, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live')
g = co.Game(co.Mem(co.find_pid()))
g.mission_state()
name = sys.argv[1] if len(sys.argv) > 1 else 'check'
for n in range(int(sys.argv[2]) if len(sys.argv) > 2 else 1):
    t0 = time.perf_counter()
    intel = g.intel()
    im = ImageGrab.grab(include_layered_windows=True)
    intel2 = g.intel()
    dt = (time.perf_counter() - t0) * 1000
    sam = intel['sam']
    s = 1080 / 1080 * hud.UI_SCALE
    H = int(hud.H_RADAR * s)
    top = 1080 - H
    # radar box (design px) as radar_tab lays it out with a one-line HERE strip
    by = max(80 + 84 + 8 + 40 + 8, hud.H_RADAR - hud.FOOT - 10 - 380)
    cx, cy, R = (hud.X0 + 180) * s, top + (by + 190) * s, 164 * s
    d = ImageDraw.Draw(im)
    d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=(255, 0, 0))
    print('frame %d (%.0f ms between reads) sam %s yaw %d' % (n, dt, [round(v) for v in sam[0]], sam[1]))
    for gd, gd2 in zip(intel['guards'], intel2['guards']):
        dist, b, dz = co.relative(sam, gd['loc'])
        if dist > 25:
            continue
        x, y = cx + math.sin(math.radians(b)) * dist * R / 20, cy - math.cos(math.radians(b)) * dist * R / 20
        moved = math.dist(gd['loc'][:2], gd2['loc'][:2]) / 100
        print('  guard %.1fm %3.0f deg dz%+.1f -> px (%d, %d)  moved %.2fm during grab  %s' % (dist, b, dz, x, y, moved, co.guard_mood(gd)[0]))
        d.ellipse([x - 12, y - 12, x + 12, y + 12], outline=(255, 0, 0), width=2)
    im.save(os.path.join(OUT, '%s_%d.png' % (name, n)))
    im.crop((int(cx - R - 40), int(cy - R - 40), int(cx + R + 40), int(cy + R + 40))).save(os.path.join(OUT, '%s_%d_radar.png' % (name, n)))
    time.sleep(1.5)
