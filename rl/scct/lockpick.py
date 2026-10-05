"""Lock picking (the PICK LOCK minigame), solved by looking at the pins.

Manual: "press the directional keys until you see the pins within the lock being pushed upwards. Once the
lock pin is moving, keep the same directional key pressed for a few seconds and a pin will be lifted."
Each pin answers to one of 8 directions (W/A/S/D and the diagonals). Found 2026-10-06: TAPPING the right
direction shakes the pins (thousands of pixels change in the pin area), the wrong ones barely move them;
holding the right one lifts the pin. When all pins are up the game leaves the lock view by itself.

  pick_lock()  -> True when the lock view closed (picked), False after the time budget
"""
import time

from PIL import ImageChops, ImageGrab

from .controls import key

DIRS = [('w',), ('w', 'd'), ('d',), ('s', 'd'), ('s',), ('s', 'a'), ('a',), ('w', 'a')]


def _pins(im):
    return im.crop((int(im.width * 0.58), int(im.height * 0.24), int(im.width * 0.76), int(im.height * 0.56))).convert('L')


def _screen():
    return ImageGrab.grab()


def _changed(a, b, level=30):
    return sum(ImageChops.difference(a, b).histogram()[level:])


def _probe(d, seconds=1.6):
    """Tap a direction for a while; how much do the pins move?"""
    base, peak, t0 = _pins(_screen()), 0, time.time()
    while time.time() - t0 < seconds:
        for k in d:
            key(k, False)
        time.sleep(0.12)
        for k in d:
            key(k, True)
        time.sleep(0.08)
        peak = max(peak, _changed(_pins(_screen()), base))
    return peak


def _centre(im):
    return im.crop((int(im.width * 0.45), int(im.height * 0.2), int(im.width * 0.78), int(im.height * 0.75))).convert('L').resize((120, 110))


def in_lock_view(ref):
    """Still looking at the lock? The big bright lock fills the centre of the lock view; once it is gone
    (more than a quarter of the centre changed) the game is back in gameplay. (Comparing the whole, mostly
    dark screen missed the exit and the solver walked Sam around 'probing'.)"""
    return _changed(_centre(_screen()), ref, level=40) < 120 * 110 // 4


def pick_lock(budget_s=150, log=print):
    ref = _centre(_screen())
    t0, lifted = time.time(), 0
    while time.time() - t0 < budget_s:
        scores = []
        for d in DIRS:
            if not in_lock_view(ref):
                log('lock picked (%d pins, %.0f s)' % (lifted, time.time() - t0))
                return True
            p = _probe(d)
            scores.append((p, d))
            if p > 800:                                   # this pin answers to d: hold it to lift the pin
                for k in d:
                    key(k, False)
                time.sleep(3.5)
                for k in d:
                    key(k, True)
                time.sleep(0.4)
                lifted += 1
                log('pin %d lifted with %s (shake %d)' % (lifted, '+'.join(d), p))
                break
        else:
            best = max(scores)
            if best[0] > 150:                             # weak answer: hold the best one anyway
                for k in best[1]:
                    key(k, False)
                time.sleep(3.5)
                for k in best[1]:
                    key(k, True)
                time.sleep(0.4)
    log('lock not picked within %d s (%d pins lifted)' % (budget_s, lifted))
    return not in_lock_view(ref)
