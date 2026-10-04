"""Menu keys by scancode: python mkey.py up up enter ..."""
import sys, time
exec(open('sam.py', encoding='utf-8').read().split("if __name__ == '__main__':")[0])
import cheat_overlay as co
h = co.game_window(co.find_pid())
if u32.GetForegroundWindow() != h:
    co.force_foreground(h); time.sleep(0.4)
CODES = {'up': (0x48, 1), 'down': (0x50, 1), 'left': (0x4B, 1), 'right': (0x4D, 1), 'enter': (0x1C, 0), 'esc': (0x01, 0),
         'space': (0x39, 0), 'w': (0x11, 0), 's': (0x1F, 0), 'a': (0x1E, 0), 'd': (0x20, 0)}
for name in sys.argv[1:]:
    code, ext = CODES[name]
    for up in (False, True):
        i = INPUT(type=1)
        i.ki = KEYBDINPUT(0, code, 0x0008 | (0x0001 if ext else 0) | (0x0002 if up else 0), 0, 0)
        u32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))
        time.sleep(0.1)
    time.sleep(0.4)
