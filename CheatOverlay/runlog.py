"""Run recorder: every mission played leaves a compact track in Saved Games\\OPSAT\\runs - the training set
for routes that work.

One JSON line every 0.5 s while a mission is live: time, room, Sam (x, y, z, camera yaw), alarm stage and the
guards within 30 m (x, y, z, yaw, mood). Objective changes are their own lines. Samples taken while the bot
controller (tools/bot) is driving carry "b":1 - it touches runs/bot_active while it plays. Read-only
telemetry, local files, nothing leaves the machine.
"""
import json
import os
import time

RUNS = os.path.join(os.path.expanduser('~'), 'Saved Games', 'OPSAT', 'runs')
MOODS = {'CALM': 0, 'SUSPICIOUS': 1, 'ALERT': 2, 'OUT': 3, 'DEAD': 4}
PERIOD_S, NEAR_M = 0.5, 30.0
BOT_FLAG = os.path.join(RUNS, 'bot_active')


def bot_driving():
    try:
        return time.time() - os.path.getmtime(BOT_FLAG) < 90
    except OSError:
        return False


class RunLog:
    def __init__(self):
        self.f = self.mission = self.objs = None
        self.last = 0.0

    def tick(self, mission, room, objs, intel, mood, relative):
        """Call every poll. mood(g) -> (label, colour); relative(sam, loc) -> (metres, bearing, dz)."""
        if not mission or mission == 'menu' or not intel:
            return
        now = time.time()
        if mission != self.mission:  # a new mission starts a new file (reloads of the same one continue it)
            self.close()
            player = 'bot' if bot_driving() else 'fisher'
            try:
                os.makedirs(RUNS, exist_ok=True)
                self.f = open(os.path.join(RUNS, '%s_%s_%s.jsonl' % (mission, time.strftime('%Y%m%d_%H%M%S'),
                                                                     player)), 'a', encoding='utf-8')
            except OSError:
                return
            self.mission, self.objs = mission, None
            self.write({'t': round(now, 2), 'start': mission, 'player': player})
        status = {o[0]: o[1] for o in objs if o[0]}
        if status and status != self.objs:
            self.write({'t': round(now, 2), 'objs': status})
            self.objs = status
        if now - self.last < PERIOD_S:
            return
        self.last = now
        sam = intel['sam']
        guards = []
        for g in intel.get('guards', []):
            if relative(sam, g['loc'])[0] <= NEAR_M:
                x, y, z = g['loc']
                guards.append([round(x), round(y), round(z), g['yaw'], MOODS.get(mood(g)[0], 0)])
        (x, y, z), yaw = sam
        rec = {'t': round(now, 2), 'r': room, 'p': [round(x), round(y), round(z), yaw],
               'a': intel.get('alarm') or 0, 'g': guards}
        if bot_driving():
            rec['b'] = 1
        self.write(rec)

    def write(self, rec):
        try:
            self.f.write(json.dumps(rec, separators=(',', ':')) + '\n')
            self.f.flush()
        except (OSError, ValueError, AttributeError):
            pass

    def close(self):
        if self.f:
            self.f.close()
        self.f = self.mission = None
