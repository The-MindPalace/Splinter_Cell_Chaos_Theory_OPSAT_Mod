"""Record the game window to MP4 while a human plays, for the training notes.

    python record_video.py [minutes=75] [fps=4]

Writes %USERPROFILE%/Saved Games/OPSAT/runs/video/<start>/seg_NN.mp4 (10-minute segments, so a crash loses at
most one) and frames.csv (segment, frame, unix time) to line frames up with OPSAT's run log ('t' field).
Stops after `minutes`, when the game closes, or when a file named STOP appears in the session folder.
"""
import csv
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import cv2
import numpy as np
from PIL import ImageGrab

TITLE = "Tom Clancy's Splinter Cell Chaos Theory"
W, H = 960, 540
SEG_S = 600

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()


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


def main():
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 75
    fps = float(sys.argv[2]) if len(sys.argv) > 2 else 4
    out = os.path.join(os.environ['USERPROFILE'], 'Saved Games', 'OPSAT', 'runs', 'video',
                       time.strftime('%Y%m%d_%H%M%S'))
    os.makedirs(out, exist_ok=True)
    log = open(os.path.join(out, 'frames.csv'), 'w', newline='')
    cw = csv.writer(log)
    cw.writerow(['seg', 'frame', 't'])
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
            time.sleep(max(0.0, 1 / fps - (time.time() - tick)))
    finally:
        if writer:
            writer.release()
        log.close()
        print('stopped after %.1f min' % ((time.time() - t0) / 60), flush=True)


if __name__ == '__main__':
    main()
