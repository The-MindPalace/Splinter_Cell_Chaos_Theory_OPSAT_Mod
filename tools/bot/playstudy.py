"""Summarise a recorded human run (OPSAT run log) into what the bot should learn from it.

    python playstudy.py RUN.jsonl [RUN2.jsonl ...]

Per room: time, crouched share, light, pace, cheat share. Events in order: objectives, guard mood changes
(suspicious / alert / knocked out / killed) with Sam's distance, alarm, cheat toggles, climbs and drops,
waits (still for 4 s+), reloads (Sam jumps somewhere else). Stretches with cheats on are marked [map only]:
their route counts as level geometry, not as stealth style.
"""
import json
import math
import os
import sys

MOOD = {0: 'calm', 1: 'suspicious', 2: 'alert', 3: 'out', 4: 'dead'}
OBJ_FILE = os.path.join(r'C:\Program Files (x86)\Steam\steamapps\common\Splintercell Chaos Theory',
                        'CheatOverlay', 'objectives.json')


def objective_texts(mission):
    try:
        d = json.load(open(OBJ_FILE, encoding='utf-8'))
    except OSError:
        return {}
    num = mission.split('_')[0]
    for k, v in d.items():
        if k.startswith('P_%s_' % num):
            return v
    return {}


def dist(a, b):
    return math.dist(a[:3], b[:3])


def flat(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def study(path):
    recs = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
    mission = next((r['start'] for r in recs if 'start' in r), '?')
    texts = objective_texts(mission)
    t0 = recs[0]['t']
    clock = lambda t: '%02d:%02d' % divmod(int(t - t0), 60)
    print('=' * 100)
    print(os.path.basename(path), '|', mission, '| %d samples, %.1f min' % (
        sum('p' in r for r in recs), (recs[-1]['t'] - t0) / 60))
    events, rooms = [], []
    prev, guards, objs, still_since = None, [], {}, None
    for r in recs:
        t = r['t']
        if 'objs' in r:
            for k, v in r['objs'].items():
                if objs.get(k) != v:
                    state = {0: 'NEW', 1: 'DONE', 2: 'FAILED'}.get(v, v)
                    events.append((t, 'OBJECTIVE %s: %s' % (state, texts.get(k, k))))
            objs = dict(r['objs'])
            continue
        if 'p' not in r:
            continue
        p, room, cheat = r['p'], r.get('r') or '?', r.get('x', 0)
        tag = ' [map only]' if cheat else ''
        # room stretches
        if not rooms or rooms[-1]['room'] != room:
            rooms.append({'room': room, 't0': t, 't1': t, 'n': 0, 'c': 0, 'l': [], 'x': 0, 'd': 0.0,
                          'g': 99.0, 'at': p[:3]})
        st = rooms[-1]
        st['t1'] = t
        st['n'] += 1
        st['c'] += r.get('c', 0)
        if 'l' in r:
            st['l'].append(r['l'])
        st['x'] += bool(cheat)
        live = [g for g in r['g'] if g[4] < 3]
        if live:
            st['g'] = min(st['g'], min(dist(p, g) for g in live) / 100)
        if prev:
            pp = prev['p']
            gap, moved = t - prev['t'], dist(p, pp)
            if moved > 800 and gap < 3 or gap > 20:
                events.append((t, 'RELOAD/JUMP to %s (%d,%d,%d) - %.0f m away, %.0f s gap' % (
                    room, p[0], p[1], p[2], moved / 100, gap)))
            else:
                st['d'] += moved
                dz = p[2] - pp[2]
                if dz > 120 and flat(p, pp) < 300:
                    events.append((t, 'climb +%d cm at (%d,%d,%d) in %s%s' % (dz, pp[0], pp[1], pp[2], room, tag)))
                elif dz < -250:
                    events.append((t, 'drop %d cm at (%d,%d,%d) in %s%s' % (dz, pp[0], pp[1], pp[2], room, tag)))
            if cheat != prev.get('x', 0):
                events.append((t, 'cheats %s' % ({0: 'OFF', 1: 'god', 2: 'invisible', 3: 'god+invisible'}[cheat])))
            if r.get('a', 0) != prev.get('a', 0):
                events.append((t, 'ALARM stage %s -> %s%s' % (prev.get('a', 0), r.get('a', 0), tag)))
            if r.get('c') is not None and prev.get('c') is not None and r['c'] != prev['c']:
                pass
            # waits
            if moved < 15:
                if still_since is None:
                    still_since = (prev['t'], pp, r.get('l'), min((dist(p, g) for g in live), default=9e9) / 100)
            else:
                if still_since and t - still_since[0] >= 4:
                    s0, sp, sl, sg = still_since
                    events.append((s0, 'waited %.0f s at (%d,%d,%d) %s, light %s, %s, nearest guard %.0f m%s' % (
                        t - s0, sp[0], sp[1], sp[2], room, sl, 'crouched' if prev.get('c') else 'standing',
                        sg, tag)))
                still_since = None
        # guard tracking: match to last sample's guards by nearest position
        new = []
        for g in r['g']:
            best = min(guards, key=lambda o: dist(o, g), default=None)
            if best is not None and dist(best, g) < 200:
                if best[4] != g[4]:
                    events.append((t, 'guard %s -> %s at (%d,%d,%d), Sam %.1f m away, light %s, %s%s' % (
                        MOOD.get(best[4]), MOOD.get(g[4]), g[0], g[1], g[2], dist(p, g) / 100, r.get('l'),
                        'crouched' if r.get('c') else 'standing', tag)))
            new.append(g)
        guards = new
        prev = r
    print('\nROOMS (in order)')
    print('%-6s %-26s %6s %8s %6s %7s %6s %9s' % ('at', 'room', 'secs', 'crouch%', 'light', 'pace', 'cheat%',
                                                  'closest'))
    for s in rooms:
        secs = s['t1'] - s['t0']
        if secs < 2:
            continue
        light = sum(s['l']) / len(s['l']) if s['l'] else float('nan')
        print('%-6s %-26s %6.0f %7.0f%% %6.1f %5.0fcm/s %5.0f%% %7.1f m' % (
            clock(s['t0']), s['room'][:26], secs, 100 * s['c'] / s['n'], light, s['d'] / max(secs, 1),
            100 * s['x'] / s['n'], s['g']))
    print('\nEVENTS')
    for t, e in sorted(events, key=lambda e: e[0]):
        print(clock(t), e)


if __name__ == '__main__':
    for f in sys.argv[1:]:
        study(f)
