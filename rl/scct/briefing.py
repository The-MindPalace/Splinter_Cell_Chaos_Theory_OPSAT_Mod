"""Mission awareness: what the objectives say, what is coming next, and what to expect in this room.

Sources (all already in OPSAT, see CheatOverlay/*.json, plus live game memory):
  hints.json        per mission, per 3D-map room: 'next' (the immediate goal) and 'ways' (how players do it)
  walkthrough.json  per objective: where it is and the ways to do it
  objectives.json   objective texts (keys -> English)
  rules.json        mission rules (fail conditions, notes such as "no alarm system on site")
  game memory       objective status (new / done / failed), the room Sam is in, and every interactable with its
                    position: vents (crawlspaces), doors, generators, computers, objective objects (crates to
                    scan), Morgenholt, NPC conversation zones

Briefing turns that into events ("new objective: ...", "objective done", "entered Wine Cellar - next: ...")
and into expectations the playbook acts on:
  guards      notes mention guards/gunman/shooter        -> sneak pace, prefer shadows, wait rather than push
  talk        a scripted conversation ("until they stop talking") -> wait it out before crossing
  noise       the notes warn about footsteps / sound meter -> slowest pace
  crawl       a vent nearby                               -> its entry/exit are known crawl moves (Space)
  climb       notes mention climbing / ledges / jumping   -> climbs are expected, not a last resort
  edge        a bridge / ledge / drop                     -> no detours off the walkway
  light       a generator or lights to shoot              -> darkness can be made
  lock        a lock to pick                              -> doors need Space and patience
  scan        objective objects to scan nearby            -> Space at them when no guard is close
  nofight     "can't be saved" / "don't break stealth"    -> never fight here
"""
import json
import math
import os

from .game import co

OVERLAY = r'C:\Program Files (x86)\Steam\steamapps\common\Splintercell Chaos Theory\CheatOverlay'
FLAGS = ['guards', 'talk', 'noise', 'crawl', 'climb', 'edge', 'light', 'lock', 'scan', 'nofight']
WORDS = {
    'guards': ('guard', 'gunman', 'shooter', 'operator', 'sentry', 'patrol', 'man at'),
    'talk': ('talking', 'stop talking', 'conversation', 'chat', 'the talk', 'let the radio operator talk'),
    'noise': ('noise', 'footsteps', 'sound meter', 'creak', 'quiet'),
    'crawl': ('crawl', 'vent', 'crawlspace'),
    'climb': ('climb', 'ledge', 'jump', 'shimmy', 'pipe', 'hop onto', 'drop down'),
    'edge': ('bridge', 'ledge', 'cliff', 'drop', 'rail'),
    'light': ('generator', 'light', 'lamp', 'bulb', 'floodlight', 'ocp'),
    'lock': ('lock', 'pick'),
    'scan': ('scan', 'crate', 'bar code'),
    'nofight': ("can't be saved", 'cannot be saved', "don't break stealth", 'do not compromise'),
}
INTERACT = {'EVentInteraction': 'vent', 'EDoorInteraction': 'door', 'EGeneratorInteraction': 'generator',
            'EComputerInteraction': 'computer', 'ECustomObjectiveInteraction': 'objective object',
            'EMorgenholtInteraction': 'Morgenholt', 'ESearchFileInteraction': 'file cabinet',
            'ETriggerInteraction': 'trigger', 'EMedKitInteraction': 'medkit', 'ENpcZoneInteraction': 'npc zone'}


def _load(name):
    try:
        return json.load(open(os.path.join(OVERLAY, name), encoding='utf-8'))
    except (OSError, ValueError):
        return {}


class Briefing:
    def __init__(self, log=print):
        self.log = log
        self.hints = {k.lower(): v for k, v in _load('hints.json').get('missions', {}).items()}
        self.walk = {k.lower(): v for k, v in _load('walkthrough.json').get('missions', {}).items()}
        self.rules = {k.lower(): v for k, v in _load('rules.json').get('missions', {}).items()}
        self.packages = _load('objectives.json')   # package (e.g. P_01_Lighthouse_Objectives) -> {key: text}
        self.texts = {}
        self.mission = self.room = None
        self.status = {}
        self.things = []                 # [(kind, name, (x, y, z))]
        self.expect = set()
        self.events = []

    # --- reading the game ------------------------------------------------------------------------------
    def scan_interactables(self, g):
        """Every interactable in the level with its position (once per mission)."""
        loc = g.props[('Actor', 'Location')][0]
        out = []
        for o in g.objects():
            kind = INTERACT.get(g.oname(g.m.u32(o + co.O_CLASS)))
            if kind:
                p = g._vec(o + loc)
                if p:
                    out.append((kind, g.oname(o), p))
        self.things = out
        return out

    def vents(self):
        """Crawlspaces: vent interactions paired by proximity -> [(entry, exit)] (both ways usable)."""
        vs = [p for k, _, p in self.things if k == 'vent']
        pairs, used = [], set()
        for i, a in enumerate(vs):
            if i in used:
                continue
            j = min((j for j in range(len(vs)) if j != i and j not in used), default=None,
                    key=lambda j: math.dist(a, vs[j]))
            if j is not None and math.dist(a, vs[j]) < 1200:
                pairs.append((a, vs[j]))
                used |= {i, j}
        return pairs

    def near(self, p, kinds, radius_cm):
        return sorted(((math.dist(p, q), k, n, q) for k, n, q in self.things if k in kinds and math.dist(p, q) < radius_cm),
                      key=lambda t: t[0])

    # --- update ----------------------------------------------------------------------------------------
    def update(self, snap, g=None):
        """Call every decision with Game.snapshot(). Returns the new events (strings)."""
        ev = []
        mission = snap.get('mission')
        if mission != self.mission:
            self.mission, self.room, self.status = mission, None, {}
            # objective keys repeat across missions: take the texts from this mission's own package
            self.texts = next((v for k, v in self.packages.items() if mission and mission.lower() in k.lower()), {})
            if g is not None:
                self.scan_interactables(g)
            r = self.rules.get((mission or '').lower(), {})
            for note in r.get('notes', []) + r.get('fail', []):
                ev.append('mission rule: ' + note)
        for key, st, *_ in snap.get('objs', []):
            if not key:
                continue
            old = self.status.get(key)
            if old is None and self.status is not None:
                if st == 0:
                    ev.append('objective: ' + self.texts.get(key, key))
            elif old != st:
                ev.append(('objective DONE: ' if st == 1 else 'objective FAILED: ' if st == 2 else 'objective changed: ')
                          + self.texts.get(key, key))
            self.status[key] = st
        room = snap.get('room')
        if room and room != self.room:
            self.room = room
            m = self.hints.get((mission or '').lower(), {}).get('rooms', {}).get(room, {})
            self.expect = self._expectations(m, snap)
            ev.append('entered %s - next: %s | expect: %s' % (room, m.get('next', '(no notes)'),
                                                              ', '.join(sorted(self.expect)) or 'nothing noted'))
        for e in ev:
            self.log('BRIEFING ' + e)
        self.events += ev
        return ev

    def _expectations(self, notes, snap):
        text = ' '.join([notes.get('next', '')] + notes.get('ways', [])).lower()
        flags = {f for f, words in WORDS.items() if any(w in text for w in words)}
        sam = snap['sam'][0]
        if self.near(sam, ('vent',), 2500):
            flags.add('crawl')
        if self.near(sam, ('objective object',), 2000):
            flags.add('scan')
        if self.near(sam, ('generator',), 2000):
            flags.add('light')
        return flags

    # --- for the agent ---------------------------------------------------------------------------------
    def vector(self):
        """Expectation flags as floats (fixed order FLAGS) for the RL observation."""
        return [1.0 if f in self.expect else 0.0 for f in FLAGS]

    def objective_hint(self, key):
        """The walkthrough's 'where' for an objective key, if known."""
        o = self.walk.get((self.mission or '').lower(), {}).get('objectives', {}).get(key, {})
        return o.get('where')
