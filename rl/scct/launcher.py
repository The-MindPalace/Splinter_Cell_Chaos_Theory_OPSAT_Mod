"""Start the game and get Sam back into the mission with nobody at the PC.

Launch path, learned the hard way (2026-10-05):
  * starting splintercell3.exe directly leaves a process with no window (the Steam DRM wrapper stalls);
    while any such process exists Steam refuses launches with a "Game already running" dialog
  * so: Steam must be running (started -silent after a reboot), hung windowless game processes are killed
    first, then `steam -applaunch 13570`; Steam's install script runs through the Steam Client Service
    (no elevation prompt seen) and its interstitials continue by themselves

Nothing is pressed blind: every step looks first.
  * other windows that pop up while launching are logged with a screenshot; plain Windows dialogs
    (Yes/OK/Allow/Play/Continue buttons) are answered with BM_CLICK, no mouse or focus needed;
    an elevation prompt is reported (needs a human) - the launch then waits for it to go away
  * menus: each screen is recognised by comparing a small thumbnail of the game window with the
    references in rl/scct/menu_refs (captured from a supervised launch); a key is pressed only for a
    recognised screen, then the screen must change before the next key
  * bounded: every wait has a timeout, every key has a budget; failure returns False with the reason
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import time

from .game import co
from .controls import tap

u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
GAME_DIR = r'C:\Program Files (x86)\Steam\steamapps\common\Splintercell Chaos Theory\System'
GAME_EXE = os.path.join(GAME_DIR, 'splintercell3.exe')
STEAM_EXE = r'C:\Program Files (x86)\Steam\steam.exe'
APP_ID = '13570'
HERE = os.path.dirname(os.path.abspath(__file__))
REFS = os.path.join(HERE, 'menu_refs')
LOG_DIR = os.path.join(os.path.expanduser('~'), 'Saved Games', 'OPSAT', 'runs', 'launch')
YES = ('yes', '&yes', 'ok', 'allow', '&allow', 'play', 'continue', 'launch', 'open', 'run')
WM_GETTEXT, BM_CLICK = 0x000D, 0x00F5


def log(*a):
    print(time.strftime('%H:%M:%S'), '[launch]', *a, flush=True)


def _text(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def _cls(hwnd):
    buf = ctypes.create_unicode_buffer(128)
    u32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _exe(pid):
    h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return '?'
    try:
        buf, n = ctypes.create_unicode_buffer(512), wt.DWORD(512)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value).lower()
        return '?'
    finally:
        k32.CloseHandle(h)


def top_windows():
    """Visible top-level windows with a title or a dialog class: [(hwnd, pid, exe, class, title)]."""
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if u32.IsWindowVisible(hwnd):
            t, c = _text(hwnd), _cls(hwnd)
            if t or c == '#32770':
                p = wt.DWORD()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
                out.append((hwnd, p.value, _exe(p.value), c, t))
        return True
    u32.EnumWindows(cb, 0)
    return out


def _buttons(hwnd):
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        if _cls(h).lower() == 'button' and u32.IsWindowVisible(h) and u32.IsWindowEnabled(h):
            found.append((h, _text(h)))
        return True
    u32.EnumChildWindows(hwnd, cb, 0)
    return found


def screenshot(tag):
    try:
        from PIL import ImageGrab
        os.makedirs(LOG_DIR, exist_ok=True)
        p = os.path.join(LOG_DIR, time.strftime('%Y%m%d_%H%M%S_') + tag + '.png')
        im = ImageGrab.grab()
        im.resize((im.width // 2, im.height // 2)).save(p)
        return p
    except Exception as e:
        return 'no screenshot (%r)' % e


class PopupWatch:
    """Notices windows that were not there before the launch; answers plain dialogs, reports the rest."""
    def __init__(self):
        self.known = {w[0] for w in top_windows()}

    def poll(self):
        for hwnd, pid, exe, cls, title in top_windows():
            if hwnd in self.known:
                continue
            self.known.add(hwnd)
            if exe == co.EXE:
                continue                                     # the game's own window
            shot = screenshot('popup')
            log('new window: %s pid %d class %s title %r (%s)' % (exe, pid, cls, title, shot))
            if exe == 'consent.exe':
                log('elevation prompt: only a person can answer it; waiting for it to close')
                continue
            if exe in ('steam.exe', 'steamwebhelper.exe'):
                # Steam's own dialogs are web views without Win32 buttons. Only a dialog about this game is
                # closed (its error notices, e.g. "Game already running"); Steam's main window, "Launching...",
                # toasts and the rest are left alone
                if 'splinter cell' in title.lower():
                    log('closing Steam dialog %r' % title)
                    u32.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE
                continue
            for b, text in _buttons(hwnd):
                if text.replace('&', '').strip().lower() in YES:
                    log('answering %r: %r' % (title, text))
                    u32.PostMessageW(b, BM_CLICK, 0, 0)
                    break


def _wait_pid(timeout, watch):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        pid = co.find_pid()
        if pid and co.game_window(pid):
            return pid
        watch.poll()
        time.sleep(1)
    return None


def _running(exe):
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ' + exe, '/NH'], capture_output=True, text=True).stdout
    return exe.lower() in out.lower()


def ensure_steam(watch):
    """The game's Steam DRM needs the Steam client running and signed in (it is not after a reboot)."""
    if _running('steam.exe'):
        return True
    log('Steam is not running: starting it (silent)')
    subprocess.Popen([STEAM_EXE, '-silent'], close_fds=True)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 90:                    # signed in once the web helper is up and settled
        if _running('steamwebhelper.exe') and time.monotonic() - t0 > 25:
            time.sleep(10)
            watch.known |= {w[0] for w in top_windows()}  # Steam's own startup windows are not popups
            return True
        time.sleep(2)
    log('Steam did not come up in 90 s')
    return False


def kill_hung():
    """A game process without a window is hung, and Steam refuses every launch while it exists
    ("Game already running", AppError_16). Kill it and wait until it is gone."""
    pid = co.find_pid()
    if not pid or co.game_window(pid):
        return
    log('game process %d has no window: killing it' % pid)
    subprocess.run(['taskkill', '/F', '/IM', co.EXE], capture_output=True)
    t0 = time.monotonic()
    while co.find_pid() and time.monotonic() - t0 < 20:
        time.sleep(1)
    time.sleep(3)                                        # let Steam notice the process is gone


def start_game(watch=None):
    """Start the game process; returns its pid or None."""
    watch = watch or PopupWatch()
    pid = co.find_pid()
    if pid:
        t0 = time.monotonic()
        while time.monotonic() - t0 < 20 and not co.game_window(pid):
            time.sleep(1)
        if co.game_window(pid):
            return pid
        kill_hung()
    ensure_steam(watch)
    for attempt in range(2):
        log('launching through Steam (-applaunch %s), attempt %d' % (APP_ID, attempt + 1))
        subprocess.Popen([STEAM_EXE, '-applaunch', APP_ID], close_fds=True)
        pid = _wait_pid(90, watch)
        if pid:
            return pid
        kill_hung()
    return None


# --- recognising screens --------------------------------------------------------------------------------
def frame_thumb(pid):
    """32x18 grayscale thumbnail of the game window (list of ints), or None."""
    from PIL import ImageGrab
    hwnd = co.game_window(pid)
    if not hwnd:
        return None
    r = wt.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    if r.right - r.left < 100:
        return None
    im = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom)).convert('L').resize((32, 18))
    return list(im.getdata())


def _dist(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def load_refs():
    refs = {}
    if os.path.isdir(REFS):
        for f in os.listdir(REFS):
            if f.endswith('.json'):
                refs[f[:-5]] = json.load(open(os.path.join(REFS, f)))
    return refs


def classify(thumb, refs, tol=14):
    """Name of the closest reference screen, if close enough; else 'unknown'."""
    best = min(((_dist(thumb, v), k.rstrip('0123456789')) for k, v in refs.items()), default=None)
    return best[1] if best and best[0] < tol else 'unknown'


def save_ref(pid, name):
    os.makedirs(REFS, exist_ok=True)
    t = frame_thumb(pid)
    n = sum(1 for f in os.listdir(REFS) if f.rstrip('.json').rstrip('0123456789') == name)
    json.dump(t, open(os.path.join(REFS, '%s%d.json' % (name, n)), 'w'))
    return t


def in_mission(pid):
    try:
        g = co.Game(co.Mem(pid))
        mission, room, objs = g.mission_state()
        return bool(mission not in (None, 'menu') and objs and room)
    except Exception:
        return False


# what to press on each recognised screen (main menu opens on CONTINUE = the latest save)
ACTION = {'movie': 'esc', 'solo': 'enter', 'main': 'enter'}


def to_mission(pid, timeout=240):
    """From wherever the game is (intro movies, menus, loading) into the mission, looking before each key.
    Unknown screens get a single Esc after 20 s of no change (skips a movie); never repeated blind."""
    refs = load_refs()
    if not refs:
        return to_mission_sequence(pid, timeout)
    t0, last, last_change, presses, esc_unknown = time.monotonic(), None, time.monotonic(), 0, 0
    hwnd = co.game_window(pid)
    while time.monotonic() - t0 < timeout:
        if in_mission(pid):
            log('in the mission after %.0f s, %d keys' % (time.monotonic() - t0, presses))
            return True
        if not co.find_pid():
            log('game exited')
            return False
        th = frame_thumb(pid)
        if th is None:
            time.sleep(2)
            continue
        if last is None or _dist(th, last) > 6:
            last, last_change = th, time.monotonic()
        screen = classify(th, refs)
        key = ACTION.get(screen)
        if screen == 'unknown' and time.monotonic() - last_change > 20 and esc_unknown < 3:
            key, esc_unknown = 'esc', esc_unknown + 1
        if key and presses < 12:
            if u32.GetForegroundWindow() != hwnd:
                co.force_foreground(hwnd)
                time.sleep(0.5)
            log('screen %s -> %s' % (screen, key))
            tap(key, 0.1)
            presses += 1
            # wait for the screen to change (or 8 s) before looking again
            t1 = time.monotonic()
            while time.monotonic() - t1 < 8:
                time.sleep(0.5)
                th2 = frame_thumb(pid)
                if th2 is None or _dist(th2, th) > 6:
                    break
            last, last_change = None, time.monotonic()
        else:
            time.sleep(1.5)
    log('not in the mission after %d s (%s)' % (timeout, screenshot('stuck')))
    return False


def _changed(pid, before, wait_s=8):
    t1 = time.monotonic()
    while time.monotonic() - t1 < wait_s:
        time.sleep(0.5)
        th = frame_thumb(pid)
        if th is None or before is None or _dist(th, before) > 6:
            return True
    return False


def to_mission_sequence(pid, timeout=240):
    """No menu references yet: the known sequence (movies -> Esc, Game mode SOLO -> Enter, main menu opens on
    CONTINUE -> Enter), each key only once the screen has been still for 3 s, each screen saved under
    menu_refs/seen/ so the references can be labelled from a real launch."""
    seen = os.path.join(REFS, 'seen')
    os.makedirs(seen, exist_ok=True)
    plan = ['esc', 'esc', 'esc', 'enter', 'enter']
    hwnd = co.game_window(pid)
    t0, last, still_since, n = time.monotonic(), None, time.monotonic(), 0
    while time.monotonic() - t0 < timeout:
        if in_mission(pid):
            log('in the mission after %.0f s (%d keys)' % (time.monotonic() - t0, n))
            return True
        if not co.find_pid():
            log('game exited')
            return False
        th = frame_thumb(pid)
        if th is None:
            time.sleep(2)
            continue
        if last is None or _dist(th, last) > 6:
            last, still_since = th, time.monotonic()
        elif time.monotonic() - still_since > 3 and n < len(plan):
            json.dump(th, open(os.path.join(seen, 'step%d_%s.json' % (n, plan[n])), 'w'))
            screenshot('menu_step%d' % n)
            if u32.GetForegroundWindow() != hwnd:
                co.force_foreground(hwnd)
                time.sleep(0.5)
            log('screen still -> %s (%d/%d)' % (plan[n], n + 1, len(plan)))
            tap(plan[n], 0.1)
            n += 1
            if not _changed(pid, th) and plan[n - 1] == 'esc':
                pass                                     # Esc on a still menu does nothing harmful
            last, still_since = None, time.monotonic()
        time.sleep(0.5)
    log('not in the mission after %d s (%s)' % (timeout, screenshot('stuck')))
    return False


def launch(timeout=300):
    """Game closed or open, anywhere: end up in the mission (latest save). True/False."""
    watch = PopupWatch()
    pid = start_game(watch)
    if not pid:
        log('game did not start (%s)' % screenshot('nostart'))
        return False
    return to_mission(pid, timeout)
