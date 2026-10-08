"""Contact sheets of a recorded session, each frame labelled with the run log, for frame-by-frame review.

    python framesheet.py VIDEO_DIR RUN.jsonl FROM TO STEP OUT.jpg [--hud] [--cols N] [--width PX]

FROM/TO are seconds from the run log's first line (the times playstudy.py prints). Each tile shows the log
time, room, light, crouched/standing and the nearest guard (distance, mood). --hud adds an enlarged crop of the
weapon box (ammo, OCP charge bar, noise meter) next to each frame - that is where shots, OCP use and noise show.
Key/mouse presses from inputs.csv inside each tile's time window are printed under the label.
"""
import csv
import json
import math
import os
import sys

import cv2
import numpy as np

MOODS = 'calm suspicious alert out dead'.split()


def main():
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    vdir, logf, t_from, t_to, step, out = a[0], a[1], float(a[2]), float(a[3]), float(a[4]), a[5]
    opt = sys.argv
    hud = '--hud' in opt
    cols = int(opt[opt.index('--cols') + 1]) if '--cols' in opt else (2 if hud else 4)
    tw = int(opt[opt.index('--width') + 1]) if '--width' in opt else (456 if hud else 480)
    rows = [(int(s), int(f), float(t)) for s, f, t in list(csv.reader(open(os.path.join(vdir, 'frames.csv'))))[1:]]
    inputs = []
    if os.path.exists(os.path.join(vdir, 'inputs.csv')):
        inputs = [(float(t), e, k) for t, e, k in list(csv.reader(open(os.path.join(vdir, 'inputs.csv'))))[1:]]
    recs = [json.loads(l) for l in open(logf, encoding='utf-8') if l.strip()]
    t0 = recs[0]['t']
    samples = [r for r in recs if 'p' in r]
    caps, tiles, x = {}, [], t_from
    while x <= t_to:
        want = t0 + x
        s, f, t = min(rows, key=lambda r: abs(r[2] - want))
        if s not in caps:
            caps[s] = cv2.VideoCapture(os.path.join(vdir, 'seg_%02d.mp4' % s))
        caps[s].set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, img = caps[s].read()
        if ok:
            th = tw * 9 // 16
            tile = cv2.resize(img, (tw, th))
            r = min(samples, key=lambda r: abs(r['t'] - want))
            p = r['p']
            live = [g for g in r['g'] if g[4] < 3]
            ng = min(live, key=lambda g: math.dist(p[:3], g[:3]), default=None)
            lab = '%02d:%04.1f %s l%.0f %s' % (x // 60, x % 60, (r.get('r') or '?')[:14], r.get('l', -1),
                                              'C' if r.get('c') else 'S')
            lab2 = 'guard %.1fm %s' % (math.dist(p[:3], ng[:3]) / 100, MOODS[ng[4]]) if ng else ''
            keys = ' '.join('%s%s' % (k, '' if e == 'down' else '^' if e == 'up' else '~')
                            for ti, e, k in inputs if want - step / 2 <= ti < want + step / 2)
            band = 64 if keys else 44
            cv2.rectangle(tile, (0, 0), (tw, band), (0, 0, 0), -1)
            cv2.putText(tile, lab, (6, 18), 0, 0.55, (0, 255, 255), 1)
            cv2.putText(tile, lab2, (6, 38), 0, 0.55, (0, 255, 255), 1)
            if keys:
                cv2.putText(tile, keys[:60], (6, 58), 0, 0.5, (120, 255, 120), 1)
            if hud:                                   # weapon box: ammo, OCP bar, noise meter
                box = cv2.resize(img[455:540, 760:960], (300, 128))
                pad = np.zeros((th, 300, 3), np.uint8)
                pad[:128] = box
                tile = np.hstack([tile, pad])
            tiles.append(tile)
        x += step
    if not tiles:
        sys.exit('no frames in that window')
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    cv2.imwrite(out, np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]),
                [cv2.IMWRITE_JPEG_QUALITY, 80])
    print(out, len(tiles), 'tiles')


if __name__ == '__main__':
    main()
