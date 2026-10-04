"""Type text into a game text field by scancode: python typekeys.py TEXT"""
import sys, time
exec(open('sam.py', encoding='utf-8').read().split("if __name__ == '__main__':")[0])
import cheat_overlay as co
h = co.game_window(co.find_pid())
if u32.GetForegroundWindow() != h:
    co.force_foreground(h); time.sleep(0.4)
ROW = {c: 0x10 + i for i, c in enumerate('qwertyuiop')}
ROW.update({c: 0x1E + i for i, c in enumerate('asdfghjkl')})
ROW.update({c: 0x2C + i for i, c in enumerate('zxcvbnm')})
ROW.update({c: 0x02 + i for i, c in enumerate('123456789')})
ROW['0'] = 0x0B


def press(code, shift=False):
    seq = ([(0x2A, False)] if shift else []) + [(code, False), (code, True)] + ([(0x2A, True)] if shift else [])
    for c, up in seq:
        i = INPUT(type=1); i.ki = KEYBDINPUT(0, c, 0x0008 | (0x0002 if up else 0), 0, 0)
        u32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT)); time.sleep(0.04)
    time.sleep(0.06)


for ch in sys.argv[1]:
    if ch == '<':
        press(0x0E)
    elif ch == '_':
        press(0x0C, True)
    else:
        press(ROW[ch.lower()], ch.isupper())
