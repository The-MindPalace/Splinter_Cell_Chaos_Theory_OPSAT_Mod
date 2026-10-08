"""Record the game window to MP4 while a human plays, for the training notes.

    python record_video.py [minutes=75] [fps=6] [--no-input]

Writes %USERPROFILE%/Saved Games/OPSAT/runs/video/<start>/:
  seg_NN.mp4   the game window, 10-minute segments (a crash loses at most one)
  frames.csv   segment, frame, unix time - lines frames up with OPSAT's run log ('t' field)
  inputs.csv   unix time, down/up/wheel, key or mouse button - only while the game window is in front, so typing
               in other programs is never recorded (--no-input turns it off). Mouse movement is not logged; the
               camera yaw is in the run log.
Stops after `minutes`, when the game closes, or when a file named STOP appears in the session folder.
"""
import csv
import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time

import cv2
import numpy as np
from PIL import ImageGrab

TITLE = "Tom Clancy's Splinter Cell Chaos Theory"
W, H = 960, 540
SEG_S = 600

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD)
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = (wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = (wt.HHOOK,)
user32.GetKeyNameTextW.argtypes = (wt.LONG, wt.LPWSTR, ctypes.c_int)
kernel32.GetModuleHandleW.restype = wt.HMODULE


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('vkCode', wt.DWORD), ('scanCode', wt.DWORD), ('flags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', ctypes.c_void_p)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('pt', wt.POINT), ('mouseData', wt.DWORD), ('flags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', ctypes.c_void_p)]


MOUSE = {0x201: ('down', 'LMB'), 0x202: ('up', 'LMB'), 0x204: ('down', 'RMB'), 0x205: ('up', 'RMB'),
         0x207: ('down', 'MMB'), 0x208: ('up', 'MMB')}


def window_rect():
    hwnd = user32.FindWindowW(None, TITLE)
    if not hwnd:
        return None
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    if r.right - r.left < 100:
        return None                                  # minimised
    return pt.x, pt.y, pt.x + r.right, pt.y + r.bottom


def game_in_front():
    hwnd = user32.FindWindowW(None, TITLE)
    return bool(hwnd) and user32.GetForegroundWindow() == hwnd


class InputLog:
    """Low-level keyboard + mouse hooks on their own thread; events are kept only while the game is in front."""

    def __init__(self, path, focus=game_in_front):
        self.f = open(path, 'w', newline='')
        self.w = csv.writer(self.f)
        self.w.writerow(['t', 'event', 'key'])
        self.focus, self.held, self.lock, self.tid = focus, set(), threading.Lock(), None
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _put(self, ev, key):
        if not self.focus():
            return
        if ev == 'down':
            if key in self.held:                     # auto-repeat while held
                return
            self.held.add(key)
        elif ev == 'up':
            self.held.discard(key)
        with self.lock:
            self.w.writerow([round(time.time(), 3), ev, key])

    def _key_name(self, k):
        buf = ctypes.create_unicode_buffer(32)
        lp = (k.scanCode << 16) | ((k.flags & 1) << 24)
        return buf.value if user32.GetKeyNameTextW(lp, buf, 32) else 'vk%d' % k.vkCode

    def _run(self):
        self.tid = kernel32.GetCurrentThreadId()

        def kb(code, wp, lp):
            if code == 0:
                k = ctypes.cast(lp, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                self._put('down' if wp in (0x100, 0x104) else 'up', self._key_name(k))
            return user32.CallNextHookEx(None, code, wp, lp)

        def ms(code, wp, lp):
            if code == 0:
                if wp in MOUSE:
                    self._put(*MOUSE[wp])
                elif wp == 0x20A:
                    m = ctypes.cast(lp, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    self._put('wheel', 'up' if ctypes.c_short(m.mouseData >> 16).value > 0 else 'down')
            return user32.CallNextHookEx(None, code, wp, lp)

        self._procs = (HOOKPROC(kb), HOOKPROC(ms))   # keep references alive
        mod = kernel32.GetModuleHandleW(None)
        hooks = [user32.SetWindowsHookExW(13, self._procs[0], mod, 0),
                 user32.SetWindowsHookExW(14, self._procs[1], mod, 0)]
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        for h in hooks:
            if h:
                user32.UnhookWindowsHookEx(h)

    def flush(self):
        with self.lock:
            self.f.flush()

    def close(self):
        if self.tid:
            user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)   # WM_QUIT
        self.th.join(2)
        with self.lock:
            self.f.close()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    minutes = float(args[0]) if len(args) > 0 else 75
    fps = float(args[1]) if len(args) > 1 else 6
    out = os.path.join(os.environ['USERPROFILE'], 'Saved Games', 'OPSAT', 'runs', 'video',
                       time.strftime('%Y%m%d_%H%M%S'))
    os.makedirs(out, exist_ok=True)
    log = open(os.path.join(out, 'frames.csv'), 'w', newline='')
    cw = csv.writer(log)
    cw.writerow(['seg', 'frame', 't'])
    inputs = None if '--no-input' in sys.argv else InputLog(os.path.join(out, 'inputs.csv'))
    print('recording to', out, flush=True)
    t0 = time.time()
    seg, writer, seg_t0, n, missing = -1, None, 0, 0, 0
    try:
        while time.time() - t0 < minutes * 60 and not os.path.exists(os.path.join(out, 'STOP')):
            tick = time.time()
            if writer is None or tick - seg_t0 >= SEG_S:
                if writer:
                    writer.release()
                seg += 1
                seg_t0, n = tick, 0
                writer = cv2.VideoWriter(os.path.join(out, 'seg_%02d.mp4' % seg),
                                         cv2.VideoWriter_fourcc(*'mp4v'), fps, (W, H))
            rect = window_rect()
            if rect is None:
                missing += 1
                if missing * (1 / fps) > 120:            # game gone for 2 minutes
                    print('game window gone - stopping', flush=True)
                    break
            else:
                missing = 0
                img = ImageGrab.grab(bbox=rect).resize((W, H))
                writer.write(cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR))
                cw.writerow([seg, n, round(tick, 2)])
                n += 1
                if n % 40 == 0:
                    log.flush()
                    if inputs:
                        inputs.flush()
            time.sleep(max(0.0, 1 / fps - (time.time() - tick)))
    finally:
        if writer:
            writer.release()
        log.close()
        if inputs:
            inputs.close()
        print('stopped after %.1f min' % ((time.time() - t0) / 60), flush=True)


if __name__ == '__main__':
    main()
