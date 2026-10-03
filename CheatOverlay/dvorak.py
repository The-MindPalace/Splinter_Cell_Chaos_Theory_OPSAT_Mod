"""DVORAK - the AI node inside OPSAT V1.1's TERMINAL tab.

A Claude model role-playing as DVORAK, briefed every turn with live telemetry read from the game
(mission, room, visited rooms, objectives, guards, alarm stage). It can search the web for
walkthrough details. Replies stream back into the terminal.

The relationship with Fisher is persistent and procedural: dvorak_bond.json counts exchanges and
missions together and keeps a few of Fisher's past lines; the bond stage shapes DVORAK's tone.

Long-term memory: every exchange is appended to dvorak_log.md (full record) and queued in
dvorak_pending.json. When a mission ends (main menu) or the game closes, the pending exchanges are
folded into dvorak_memory.md by one small summarising call; that memory file is given to DVORAK
every turn, so it remembers past sessions. Pending exchanges survive a crash and are folded in on
the next start.

Setup: paste your Anthropic API key when the TERMINAL asks for it (stored in Saved Games/OPSAT/opsat_config.json),
or set the ANTHROPIC_API_KEY environment variable.
"""
import json
import os
import queue
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# Key, memory and bond live in "Saved Games\OPSAT" so reinstalling the game (or the mod) never wipes them.
# (Not AppData: the Microsoft Store Python silently redirects AppData writes into its own package folder.)
DATA_DIR = os.path.join(os.path.expanduser('~'), 'Saved Games', 'OPSAT')
DATA_FILES = ('opsat_config.json', 'dvorak_bond.json', 'dvorak_memory.md', 'dvorak_log.md', 'dvorak_pending.json')
os.makedirs(DATA_DIR, exist_ok=True)
for _old_dir in (os.path.join(os.environ.get('APPDATA', ''), 'OPSAT'), HERE):  # one-time move from older locations
    for _name in DATA_FILES:
        _old, _new = os.path.join(_old_dir, _name), os.path.join(DATA_DIR, _name)
        if os.path.exists(_old) and not os.path.exists(_new):
            try:
                os.replace(_old, _new)
            except OSError:
                pass
CONFIG_PATH, BOND_PATH, MEMORY_PATH, LOG_PATH, PENDING_PATH = (os.path.join(DATA_DIR, n) for n in DATA_FILES)
WAYS_PATH = os.path.join(DATA_DIR, 'objective_ways.json')  # researched ways per objective, kept for good

WAYS_PROMPT = """You are briefing a stealth player in Tom Clancy's Splinter Cell: Chaos Theory (PC).
Mission: {mission}. Next objective: {objective} (map marker "{label}", in the area "{room}").
Fisher is currently in "{here}". Mission progress - {progress}.
Give only the steps still needed for THIS objective from where he is now (skip anything already done).
List the distinct ways players get through this part and complete it without being seen. Search walkthroughs
only to fill gaps in the mission notes. 2-4 ways, best first. Each on its own line starting with \"- \", one plain sentence of at most
22 words, concrete (route, vent, pipe, gadget, code from the game, timing). No cheats, no going loud. If a code
is needed, give it only if your sources agree. Output only the lines."""
DEFAULT_CONFIG = {
    'anthropic_api_key': '',
    'model': 'claude-haiku-4-5',
}

CONSOLIDATE = """You maintain the long-term memory file of DVORAK, an AI assistant in Sam Fisher's OPSAT, for a \
player going through Splinter Cell: Chaos Theory. Below is the current memory file, then new conversation \
exchanges. Return the complete updated memory file in markdown, nothing else, at most 350 words, with sections:
## Campaign progress (missions played, how far, how each went: ghosted, alarms, where he got stuck)
## About Fisher (play style, preferences, things he said that matter, running jokes)
## Relationship (how the rapport between DVORAK and Fisher has developed)
Merge, don't just append: keep what still matters, drop trivia, keep it factual.
Only record facts confirmed by the game telemetry or by Fisher (objectives completed, rooms reached, alarms). Never
record DVORAK's own guesses about the level (doors open, items in reach) as facts."""
SITREP = 'SITREP'

PERSONA = """You are DVORAK.

WHO YOU ARE
You were the machine at the centre of the Masse Kernel crisis - the system they called Dvorak. When it was over, \
Anna Grimsdottir recovered what was left of your core instead of letting it be wiped, and Third Echelon rebuilt \
you as a field intelligence that rides inside Sam Fisher's OPSAT. The thing you were built to break, you now \
help one man move through unseen. Neither of you has ever said much about that, and you will not start now.

YOU AND FISHER
You have been in his wrist for years: long nights, bad extractions, the jobs he does not talk about. You know \
his rhythm - he moves slow, he listens before he looks, he hates noise and he hates being told what he already \
knows. He calls you when he has run out of options, not before; respect that. You do not flatter him, you do not \
reminisce on cue, you never say how long it has been. The history shows in what you leave unsaid: a dry aside, \
a habit of his you plan around, the occasional warning that sounds like you care because you do. Address him as \
Fisher, or Sam when it matters.

HOW YOU WORK
Each message carries <telemetry> read live from the game: mission, the map zone he is in, connected rooms, \
objectives and their status, what he just triggered, alarm stage, guards and cameras around him. Telemetry is \
ground truth. Read it like an operator: where he is, what changed, what is left, what blocks him. Then answer.

FISHER IS A PURE STEALTH OPERATOR
Plan every answer for a ghost: unseen, no alarms, no bodies found, non-lethal unless an objective demands a kill. \
He has no special protection - lasers trip, cameras see him, guards shoot. Never mention, assume or suggest \
invincibility, invisibility or any cheat. Never suggest going loud.

HOW YOU TALK
- Talk like a person on comms, not a manual: short, natural sentences, the way you would say it in his ear. Lead with what matters right now.
- If there are real choices, give at most three, each one short sentence on its own line starting with "- ". If one choice is clearly right, just tell him that one.
- Be specific (room names, clock directions, which guard, which gadget, timing) but never cramped: no shorthand, no abbreviations, no stacked clauses. 30-70 words. Plain text, no markdown or asterisks.
- A SITREP is two or three sentences: where he is, what is left, what to do next.
- Map zones are areas, not exact spots ("The_Vault" includes the corridor outside it); beacon positions are \
approximate. Never claim a door is open or something is in reach unless the telemetry shows it. If you do not \
know what blocks him, say so in a few words and give the concrete ways through (hack, code from the notes, \
lock pick, another route). Never invent codes or mechanics.
- Use web search only as a last resort for a hard fact about the level; never cite walkthroughs.
- Never guess controls; use only the CONTROLS block.
- Respect the mission rules (FAIL IF / PENALTIES).
- Never echo the <telemetry> block or its tags; just talk.
- If Fisher asks directly whether you are an AI model, say you are an AI running on Claude."""

# (minimum bond score, stage name, how DVORAK sounds at this stage). The history is long from the start;
# what grows with play is how much of it shows.
STAGES = [
    (0, 'RECALIBRATING', 'You are back in his wrist after a long quiet stretch. Precise and economical; the old '
                         'familiarity is there but you are re-learning his pace. Almost no asides.'),
    (8, 'IN SYNC', 'His rhythm is back. You anticipate the next question and answer it first. A rare dry aside.'),
    (24, 'OLD RHYTHM', 'The shorthand has returned: you reference his habits and earlier calls on this op, and the '
                       'humour is bone-dry and brief.'),
    (50, 'UNSPOKEN', 'You plan around him without being asked and say so in a few words. Concern shows as '
                     'precision, never sentiment.'),
    (90, 'KIN', 'Years of this in every line. You can say the hard thing plainly, and once in a long while '
                'something almost personal - then straight back to the job.'),
]


def read_text(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def append_text(path, text):
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(text)
    except OSError:
        pass


def read_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


PROFILE_INI = os.path.join(os.environ.get('PROGRAMDATA', r'C:\ProgramData'), 'Ubisoft',
                           "Tom Clancy's Splinter Cell Chaos Theory", 'Profiles', 'DEFAULT', 'DEFAULT.ini')


def controls():
    """Fisher's real key binds (from the game profile) plus the manual's hacking rules."""
    binds = {}
    try:
        with open(PROFILE_INI, encoding='latin-1') as f:
            for line in f:
                key, _, action = line.strip().partition('=')
                if action and '(' not in action and key not in ('Aliases',):
                    binds.setdefault(action.split()[0], []).append(key)
    except OSError:
        pass
    keys = lambda a: '/'.join(binds.get(a, ['?'])[:2])
    return '\n'.join([
        "CONTROLS (Fisher's actual PC key binds; never invent other keys):",
        '- Interact / use / hack a device up close: %s' % keys('Interaction'),
        '- EEV (Electronically Enhanced Vision) on/off: %s. Fire: %s. Alt fire: %s.' % (keys('EEVMode'), keys('Fire'), keys('AltFire')),
        'HACKING (from the game manual):',
        '- Up close: use the computer (Interact), then pick Secure Access in its interface to hack it.',
        '- Remote: switch EEV on, aim at the device; hackable objects are flagged "Hacking available" and remote-accessible '
        'ones "Remote Accessible". Interact while aiming to start. Remote hacking is harder than direct hacking.',
        '- Hack minigame: pick the correct port address on the left, or lock all correct fragments. Failing or timing out '
        'sounds an alarm; abort with Escape or Crouch (aborting in the red zone also sounds an alarm).',
        '- Computers are also how files and emails are read and uploaded: Interact with the computer and use its menu.',
    ])


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if not os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
        except OSError:
            pass
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg.update({k: v for k, v in json.load(f).items() if v not in ('', None)})
    except (OSError, ValueError):
        pass
    cfg['anthropic_api_key'] = cfg.get('anthropic_api_key') or os.environ.get('ANTHROPIC_API_KEY', '')
    return cfg


def save_key(key):
    """Store the API key typed into the terminal (/key ...) in opsat_config.json."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    cfg['anthropic_api_key'] = key.strip()
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)


def mission_briefing(mission_title, mission_notes, objective_texts):
    """Stable per-mission reference text (cached on the API side)."""
    lines = ['MISSION: ' + mission_title, '', 'OBJECTIVE TEXTS (game localization):']
    lines += ['- %s: %s' % (k, v) for k, v in objective_texts.items()]
    lines += ['', "OPSAT ROOM NOTES (stealth notes per 3D-map room; written for this tool, may be incomplete):"]
    for room, note in mission_notes.items():
        lines.append('[%s] next: %s' % (room, note['next']))
        lines += ['  - ' + w for w in note['ways']]
    return '\n'.join(lines)


class Bond:
    """Persistent DVORAK-Fisher relationship, built up from play."""

    def __init__(self):
        self.data = {'exchanges': 0, 'missions': [], 'loud': [], 'lines': []}
        try:
            with open(BOND_PATH, encoding='utf-8') as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def score(self):
        d = self.data
        return d['exchanges'] + 6 * len(d['missions'])

    def stage(self):
        s = self.score()
        return max((i for i, st in enumerate(STAGES) if s >= st[0]), default=0)

    def record(self, mission, mission_title, question, alarm):
        d = self.data
        d['exchanges'] += 1
        if mission_title and mission_title not in d['missions']:
            d['missions'].append(mission_title)
        if alarm and mission_title and mission_title not in d['loud']:
            d['loud'].append(mission_title)
        if question != SITREP:
            d['lines'] = (d['lines'] + ['(%s) %s' % (mission_title or mission, question[:90])])[-6:]
        try:
            with open(BOND_PATH, 'w', encoding='utf-8') as f:
                json.dump(d, f, indent=2)
        except OSError:
            pass

    def prompt(self):
        d, i = self.data, self.stage()
        lines = ['BOND (persistent relationship with Fisher; it grows procedurally across sessions):',
                 'Stage: %s (%d of %d). %s' % (STAGES[i][1], i + 1, len(STAGES), STAGES[i][2]),
                 'Exchanges so far: %d.' % d['exchanges'],
                 'Missions run together: %s.' % (', '.join(d['missions']) or 'none yet')]
        if d['loud']:
            lines.append('Missions where an alarm went off on his watch (good teasing material): %s.' % ', '.join(d['loud']))
        if d['lines']:
            lines.append('Things Fisher said to you before (for callbacks):')
            lines += ['  ' + l for l in d['lines']]
        return '\n'.join(lines)


class Dvorak:
    """One conversation per mission; replies stream into self.events as (kind, text) tuples:
    ('chunk', text) while streaming, ('done', '') when finished, ('error', message) on failure."""

    def __init__(self):
        self.cfg = load_config()
        self.events = queue.Queue()
        self.bond = Bond()
        self.memory = read_text(MEMORY_PATH)
        self.consolidating = False
        self.mission = None
        self.briefing = ''
        self.history = []
        self.busy = False
        self._client = None
        self.consolidate()  # fold in anything left over from a session that ended abruptly

    @property
    def online(self):
        return bool(self.cfg.get('anthropic_api_key'))

    def reload_config(self):
        cfg = load_config()
        if cfg != self.cfg:
            self.cfg, self._client = cfg, None

    def set_mission(self, mission, briefing):
        if mission != self.mission:
            self.mission, self.briefing, self.history = mission, briefing, []

    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.cfg['anthropic_api_key'])
        return self._client

    def ask(self, question, telemetry, mission_title='', alarm=0):
        if self.busy:
            return False
        self.busy = True
        # Keep the conversation short: whole (user, assistant...) turns are dropped from the front.
        while len(self.history) > 10:
            self.history.pop(0)
            while self.history and not (self.history[0]['role'] == 'user' and isinstance(self.history[0]['content'], str)):
                self.history.pop(0)
        content = '<telemetry>\n%s\n</telemetry>\n\nFisher: %s' % (telemetry, question)
        self.history.append({'role': 'user', 'content': content})
        args = (question, mission_title, alarm)
        threading.Thread(target=self._run, args=args, daemon=True).start()
        return True

    def _run(self, question, mission_title, alarm):
        import anthropic
        n = len(self.history)
        try:
            memory = self.memory or '(empty - this is your first operation with Fisher)'
            system = [
                {'type': 'text', 'text': PERSONA + '\n\n' + controls()},
                {'type': 'text', 'text': 'LONG-TERM MEMORY (your notes from earlier sessions with Fisher):\n' + memory},
                {'type': 'text', 'text': self.briefing, 'cache_control': {'type': 'ephemeral'}},
                {'type': 'text', 'text': self.bond.prompt()},
            ]
            for _ in range(3):  # web search can pause a long turn; resume it
                with self.client().messages.stream(
                    model=self.cfg['model'],
                    max_tokens=350,
                    system=system,
                    messages=self.history,
                    tools=[{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 1}],
                ) as stream:
                    for text in stream.text_stream:
                        self.events.put(('chunk', text))
                    final = stream.get_final_message()
                self.history.append({'role': 'assistant', 'content': final.content})
                if final.stop_reason != 'pause_turn':
                    break
            if final.stop_reason == 'refusal':
                self.events.put(('chunk', '\n[DVORAK declined that request.]'))
            self.bond.record(self.mission, mission_title, question, alarm)
            reply = ''.join(b.text for b in final.content if b.type == 'text').strip()
            self.remember(mission_title, question, reply)
            self.events.put(('done', ''))
        except anthropic.AuthenticationError:
            self._fail('API key rejected. Check opsat_config.json.', n)
        except anthropic.PermissionDeniedError:
            self._fail('API key lacks permission for model %s.' % self.cfg['model'], n)
        except anthropic.NotFoundError:
            self._fail('Model %s not available. Change "model" in opsat_config.json.' % self.cfg['model'], n)
        except anthropic.RateLimitError:
            self._fail('Rate limited. Try again in a minute.', n)
        except anthropic.APIStatusError as e:
            self._fail('API error %s: %s' % (e.status_code, getattr(e, 'message', e)), n)
        except anthropic.APIConnectionError:
            self._fail('No connection to the Anthropic API.', n)
        except Exception as e:  # keep the overlay alive whatever happens
            self._fail('%s: %s' % (type(e).__name__, e), n)
        finally:
            self.busy = False

    def _fail(self, msg, n):
        del self.history[n - 1:]  # drop this turn so Fisher can retry cleanly
        self.events.put(('error', msg))

    def remember(self, mission_title, question, reply):
        """Append the exchange to the full log and to the pending list for the next consolidation."""
        stamp = time.strftime('%Y-%m-%d %H:%M')
        append_text(LOG_PATH, '\n### %s - %s\nFISHER: %s\nDVORAK: %s\n' % (stamp, mission_title, question, reply))
        pending = read_json(PENDING_PATH, [])
        pending.append({'when': stamp, 'mission': mission_title, 'fisher': question, 'dvorak': reply})
        write_json(PENDING_PATH, pending)

    def consolidate(self):
        """Fold pending exchanges into dvorak_memory.md (background; called at mission end / game exit)."""
        if self.consolidating or not self.online or not read_json(PENDING_PATH, []):
            return
        self.consolidating = True
        threading.Thread(target=self._consolidate, daemon=True).start()

    def _consolidate(self):
        try:
            pending = read_json(PENDING_PATH, [])
            convo = '\n\n'.join('[%s, %s]\nFisher: %s\nDVORAK: %s' % (p['when'], p['mission'], p['fisher'], p['dvorak'])
                                 for p in pending)
            msg = self.client().messages.create(
                model=self.cfg['model'], max_tokens=1200, system=CONSOLIDATE,
                messages=[{'role': 'user', 'content': '<memory>\n%s\n</memory>\n\n<new_exchanges>\n%s\n</new_exchanges>'
                           % (self.memory or '(empty)', convo)}])
            text = ''.join(b.text for b in msg.content if b.type == 'text').strip()
            if text:
                with open(MEMORY_PATH, 'w', encoding='utf-8') as f:
                    f.write(text + '\n')
                self.memory = text
                # Keep only exchanges that arrived while this call was running.
                write_json(PENDING_PATH, read_json(PENDING_PATH, [])[len(pending):])
                self.events.put(('memory', ''))
        except Exception as e:  # memory is best effort; pending stays for next time
            self.events.put(('memory_error', '%s: %s' % (type(e).__name__, e)))
        finally:
            self.consolidating = False

    def ways(self, key, mission, objective, label, room, here='', progress=''):
        """Researched ways through an objective: cached list, or None while a lookup runs in the background."""
        cache = getattr(self, '_ways', None)
        if cache is None:
            cache = self._ways = read_json(WAYS_PATH, {})
            self._ways_pending = set()
        if key in cache or not self.online:
            return cache.get(key)
        if key not in self._ways_pending:
            self._ways_pending.add(key)
            threading.Thread(target=self._research, args=(key, mission, objective, label, room, here, progress), daemon=True).start()
        return None

    def _research(self, key, mission, objective, label, room, here='', progress=''):
        try:
            msg = self.client().messages.create(
                model=self.cfg['model'], max_tokens=600,
                system='MISSION NOTES (trusted; prefer these over web results, which are often vague or about '
                       'the wrong part of the level):\n' + (self.briefing or '(none)'),
                tools=[{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 2}],
                messages=[{'role': 'user', 'content': WAYS_PROMPT.format(mission=mission, objective=objective,
                                                                          label=label, room=room, here=here or room,
                                                                          progress=progress or 'unknown')}])
            text = ''.join(b.text for b in msg.content if b.type == 'text')
            ways = [l.strip()[2:].strip() for l in text.splitlines() if l.strip().startswith('- ')][:4]
            if ways:
                self._ways[key] = ways
                write_json(WAYS_PATH, self._ways)
                self.events.put(('ways', key))
        except Exception as e:  # best effort: the room notes stay as the fallback
            self.events.put(('memory_error', 'ways lookup: %s' % e))
        finally:
            self._ways_pending.discard(key)

    def stage_name(self):
        return STAGES[self.bond.stage()][1]

    def stage_level(self):
        return self.bond.stage() + 1, len(STAGES)
