"""Top-down sketch from game memory: room centres + connections (3D map), Sam, guards, objective beacons, and the
recorded run tracks. python topdown.py NAME [radius_m]"""
import math
import os
import sys
import glob
import json

sys.path.insert(0, r'C:\Program Files (x86)\Steam\steamapps\common\Splintercell Chaos Theory\CheatOverlay')
import cheat_overlay as co
from PIL import Image, ImageDraw, ImageFont

name = sys.argv[1] if len(sys.argv) > 1 else 'map'
radius = float(sys.argv[2]) if len(sys.argv) > 2 else 60
g = co.Game(co.Mem(co.find_pid()))
mission, room, objs = g.mission_state()
intel = g.intel()
(sx, sy, sz), syaw = intel['sam']
S, PX = 1000, 1000 / (2 * radius * 100)   # image px per unit


def P(x, y):
    # screen up = Sam's camera forward, so the sketch matches the radar
    a = math.radians(syaw / 65536 * 360)
    dx, dy = x - sx, y - sy
    fwd = dx * math.cos(a) + dy * math.sin(a)
    right = -dx * math.sin(a) + dy * math.cos(a)
    return S / 2 + right * PX, S / 2 - fwd * PX


im = Image.new('RGB', (S, S), (12, 16, 14))
d = ImageDraw.Draw(im)
f = ImageFont.truetype(r'C:\Windows\Fonts\segoeui.ttf', 14)
for r in (10, 20, 40):
    d.ellipse([S / 2 - r * 100 * PX, S / 2 - r * 100 * PX, S / 2 + r * 100 * PX, S / 2 + r * 100 * PX], outline=(40, 60, 50))
rw = getattr(g, 'room_world', {})
for a, ns in getattr(g, 'room_graph', {}).items():
    for b in ns:
        if a in rw and b in rw:
            d.line([P(*rw[a][:2]), P(*rw[b][:2])], fill=(60, 80, 70))
for rn, c in rw.items():
    x, y = P(*c[:2])
    d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(120, 140, 130))
    d.text((x + 5, y - 8), '%s z%.0f' % (rn, (c[2] - sz) / 100 if len(c) > 2 else 0), fill=(150, 170, 160), font=f)
for path in glob.glob(os.path.join(os.path.expanduser('~'), 'Saved Games', 'OPSAT', 'runs', '%s_*.jsonl' % mission)):
    pts = [json.loads(l) for l in open(path, encoding='utf-8')]
    pts = [P(*p['p'][:2]) for p in pts if 'p' in p]
    if len(pts) > 1:
        d.line(pts, fill=(200, 170, 60) if 'bot' in path else (90, 160, 220), width=2)
for gd in intel['guards']:
    x, y = P(*gd['loc'][:2])
    mood = co.guard_mood(gd)[0]
    col = {'CALM': (120, 230, 140), 'SUSPICIOUS': (255, 180, 70), 'ALERT': (255, 90, 80)}.get(mood, (90, 90, 90))
    h = math.radians(gd['yaw'] / 65536 * 360 - syaw / 65536 * 360)
    d.line([(x, y), (x + math.sin(h) * 18, y - math.cos(h) * 18)], fill=col, width=2)
    d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=col)
for m in intel.get('objectives') or []:
    x, y = P(*m['loc'][:2])
    d.polygon([(x, y - 7), (x + 7, y), (x, y + 7), (x - 7, y)], outline=(134, 205, 250))
    d.text((x + 8, y - 8), m['name'], fill=(134, 205, 250), font=f)
d.polygon([(S / 2, S / 2 - 12), (S / 2 + 8, S / 2 + 8), (S / 2 - 8, S / 2 + 8)], fill=(255, 255, 255))
d.text((10, 10), '%s  room %s  Sam (%.0f, %.0f, %.0f)  up = camera forward, rings 10/20/40 m' % (mission, room, sx, sy, sz),
       fill=(220, 220, 220), font=f)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live', name + '.png')
im.save(out)
print('saved', out)
print('rooms:', {k: [round(v) for v in c[:3]] for k, c in rw.items()})
print('graph from', room, ':', getattr(g, 'room_graph', {}).get(room))
