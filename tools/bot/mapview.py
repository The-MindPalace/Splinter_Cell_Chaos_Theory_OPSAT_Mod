"""Top-down picture of what the bot knows: nav mesh, explored cells, tried moves, a target.

  python mapview.py OUT.png [x0 y0 x1 y1] [--target x y z]

Grey triangles: the AI nav mesh (lighter = higher). Dots: explored 1 m cells (colour = height).
Green ticks: open moves, red ticks: blocked, orange: climbs that worked, magenta: climbs that failed.
"""
import json
import math
import os
import sys

from PIL import Image, ImageDraw

RUNS = os.path.join(os.path.expanduser('~'), 'Saved Games', 'OPSAT', 'runs')


def main():
    args = sys.argv[1:]
    out = args.pop(0)
    target = None
    if '--target' in args:
        i = args.index('--target')
        target = tuple(float(v) for v in args[i + 1:i + 4])
        del args[i:i + 4]
    mission = '01_Lighthouse'
    nm = json.load(open(os.path.join(RUNS, 'navmesh_%s.json' % mission)))
    ex = json.load(open(os.path.join(RUNS, 'explore_%s.json' % mission)))
    cells = {tuple(json.loads(k)): v for k, v in ex['cells'].items()}
    tries = {(tuple(json.loads(k)[0]), json.loads(k)[1]): v for k, v in ex['tries'].items()}
    if args:
        x0, y0, x1, y1 = (float(v) for v in args[:4])
    else:
        xs = [v['p'][0] for v in cells.values()]
        ys = [v['p'][1] for v in cells.values()]
        x0, x1, y0, y1 = min(xs) - 500, max(xs) + 500, min(ys) - 500, max(ys) + 500
    W = 1000
    sc = W / (x1 - x0)
    H = int((y1 - y0) * sc)
    im = Image.new('RGB', (W, H), (12, 14, 12))
    d = ImageDraw.Draw(im)
    P = lambda x, y: ((x - x0) * sc, (y1 - y) * sc)          # north up
    zc = lambda z: max(40, min(255, int(120 + z / 8)))
    for verts, tris in zip(nm['verts'], nm['tris']):
        for vs, _, c in tris:
            pts = [P(verts[k][0], verts[k][1]) for k in vs]
            if all(-50 < p[0] < W + 50 and -50 < p[1] < H + 50 for p in pts):
                g = zc(c[2])
                d.polygon(pts, fill=(g // 3, g // 3, g // 3), outline=(70, 70, 70))
    for (c, s), t in tries.items():
        p = cells.get(c, {}).get('p')
        if not p:
            continue
        a = math.radians(s * 45)
        q = (p[0] + math.cos(a) * 45, p[1] + math.sin(a) * 45)
        col = {'open': (60, 170, 60), 'blocked': (210, 50, 50), 'climb_ok': (255, 160, 0),
               'climb_fail': (220, 0, 220)}.get(t['result'], (90, 90, 90))
        if t.get('src') in ('track', 'reverse') and t['result'] == 'open':
            col = (40, 90, 40)
        d.line([P(*p[:2]), P(*q)], fill=col, width=2)
    for v in cells.values():
        x, y = P(*v['p'][:2])
        g = zc(v['p'][2])
        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(g, g, 255 - g // 2))
    if target:
        x, y = P(*target[:2])
        d.line([x - 10, y - 10, x + 10, y + 10], fill=(255, 255, 0), width=3)
        d.line([x - 10, y + 10, x + 10, y - 10], fill=(255, 255, 0), width=3)
    d.text((8, 8), 'x %d..%d  y %d..%d  (1 m = %.1f px, north up)' % (x0, x1, y0, y1, sc * 100), fill=(200, 200, 200))
    im.save(out)
    print('saved', out, im.size)


if __name__ == '__main__':
    main()
