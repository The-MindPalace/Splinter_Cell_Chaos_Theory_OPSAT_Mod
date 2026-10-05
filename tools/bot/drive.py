"""Drive Sam by hand, step by step, many steps per call (for finding routes the explorer cannot).

  python drive.py STEP [STEP ...]
    nv              toggle night vision (key 2)
    head:DEG        stand still and face absolute heading DEG (0 = +x, 90 = +y)
    face:X,Y        face a world point
    turn:DEG        turn by DEG (+ right)
    pitch:DEG       look up (+) / down (-) by DEG
    w:S  a:S  s:S  d:S   hold that key S seconds (crouched walk unless 'stand')
    fast / slow     walk speed full / sneaking
    stand / crouch  stance (checked in memory)
    jump            push forward + jump (ledge climb), reports height change
    use             tap Space
    wait:S
    shot:NAME       screenshot -> live/NAME.png (960x540)
    pos             print position, room, light, height
Every position change is recorded by OPSAT's run recorder, so a route found here seeds the bot's map.
"""
import os
import sys
import time

sys.path.insert(0, r'C:\Users\prana\Documents\OPSAT_V1.1_Mod\rl')
from scct.controls import SC, key, mouse, tap  # noqa: E402
from scct.game import Game  # noqa: E402
from scct.skills import Fisher, heading_to  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live')
SC['nv'] = 0x03
PITCH_PER_DEG = 15.28                                   # same as yaw (sam_cal.json)


def pos(f):
    s = f.perceive()
    (x, y, z), yaw = s['sam']
    print('  pos %d %d %d  heading %.0f  room %s  light %.1f  crouched %s' % (
        x, y, z, (yaw / 65536 * 360) % 360, s['room'], f.game.snapshot()['light'], f.game.snapshot()['crouched']))


def main():
    g = Game()
    g.focus()
    time.sleep(0.3)
    f = Fisher(g, log=lambda *a: print('  [fisher]', *a))
    for step in sys.argv[1:]:
        name, _, arg = step.partition(':')
        print('>', step)
        if name == 'nv':
            tap('nv', 0.08)
            time.sleep(0.6)
        elif name == 'head':
            f.face_heading(float(arg))
        elif name == 'face':
            x, y = (float(v) for v in arg.split(','))
            f.face_heading(heading_to(f.perceive()['sam'][0][:2], (x, y)))
        elif name == 'turn':
            f.turn_smooth(float(arg))
        elif name == 'pitch':
            d = float(arg)
            for _ in range(max(1, int(abs(d) / 3))):
                mouse(0, -PITCH_PER_DEG * d / max(1, int(abs(d) / 3)))
                time.sleep(0.01)
        elif name in ('w', 'a', 's', 'd'):
            key(name, False)
            time.sleep(float(arg or 1))
            key(name, True)
            time.sleep(0.2)
            pos(f)
        elif name == 'fast':
            f.set_speed(1.0)
        elif name == 'slow':
            f.set_speed(0.15)
        elif name == 'stand':
            g.set_crouch(False)
        elif name == 'crouch':
            g.set_crouch(True)
        elif name == 'jump':
            z0 = f.perceive()['sam'][0][2]
            key('w', False)
            time.sleep(0.3)
            tap('jump', 0.12)
            time.sleep(1.4)
            key('w', True)
            print('  jump: %+.0f cm' % (f.perceive()['sam'][0][2] - z0))
        elif name == 'use':
            tap('use', 0.1)
            time.sleep(0.5)
        elif name == 'wait':
            time.sleep(float(arg))
        elif name == 'shot':
            from PIL import ImageGrab
            time.sleep(0.25)
            ImageGrab.grab().resize((960, 540)).save(os.path.join(OUT, arg + '.png'))
        elif name == 'pos':
            pos(f)
    g.release_all()


if __name__ == '__main__':
    main()
