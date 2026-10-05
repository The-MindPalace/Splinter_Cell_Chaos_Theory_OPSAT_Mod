"""Splinter Cell: Chaos Theory controls (PC, DEFAULT profile) - what each one is for, and when Fisher uses it.

This is the bot's manual. Bindings come from the game profile
(ProgramData\\Ubisoft\\Tom Clancy's Splinter Cell Chaos Theory\\Profiles\\DEFAULT\\DEFAULT.ini).
Inputs go in as scancodes (the game ignores virtual-key events).

| Control            | Key / input      | What it does                                   | When Fisher uses it |
|--------------------|------------------|------------------------------------------------|---------------------|
| Move               | W A S D          | walk; speed set by the wheel                   | always, at low speed |
| Look               | mouse            | camera; Sam walks where the camera points      | steering |
| Speed              | mouse wheel      | fine walk speed, many steps (never 0: stopping = let go of W) | 10-20% exploring, 2-5% near guards |
| Crouch             | C (toggle)       | lower, quieter, harder to see                  | the whole game |
| Jump / climb       | Shift            | jump; grabs ledges, pipes, chest-high walls    | only when the way forward is a ledge |
| Interact           | Space            | the action shown top-right: open door, switch, grab a guard from behind, pick up / drop a body, interrogate | contextual |
| Interaction choice | mouse wheel      | while the top-right list has several entries the wheel moves the selection (verify per prompt) | pick "Interrogate" |
| Lethal attack      | left mouse       | knife; with a guard held: kill                 | never (non-lethal play) |
| Non-lethal attack  | right mouse      | elbow strike; with a guard held: knock out     | after the grab / interrogation |
| Whistle            | V                | makes noise: a guard comes to look             | lure one guard to a dark spot |
| Night vision etc.  | (goggles)        | vision modes                                    | not needed by telemetry |
| Quicksave / load   | F5 / F8          | save / restore                                  | episode start / reset |

Telemetry that drives the decisions (OPSAT's memory reader): Sam's position, camera yaw, crouch flag, light
level on Sam (Actor.LuminosityFactor: 0-5 shadow, 35+ lit), walk speed (Pawn.GroundSpeed), every guard's
position, facing, view cone, AI state (s_Grabbed, s_Unconscious, s_Carried...) and stress, alarm stage,
objectives, the 3D map's rooms and connections.
"""
import ctypes
import ctypes.wintypes as wt
import time

u32 = ctypes.windll.user32

SC = {'w': 0x11, 'a': 0x1E, 's': 0x1F, 'd': 0x20, 'crouch': 0x2E, 'jump': 0x2A, 'use': 0x39, 'whistle': 0x2F,
      'quicksave': 0x3F, 'quickload': 0x42, 'enter': 0x1C, 'esc': 0x01}
LMB, RMB = (0x0002, 0x0004), (0x0008, 0x0010)          # (down, up) MOUSEEVENTF flags


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT)]
    _anonymous_ = ('u',)
    _fields_ = [('type', wt.DWORD), ('u', _U)]


def _send(i):
    u32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def key(name, up):
    i = INPUT(type=1)
    i.ki = KEYBDINPUT(0, SC[name], 0x0008 | (0x0002 if up else 0), 0, 0)
    _send(i)


def tap(name, hold=0.08):
    key(name, False)
    time.sleep(hold)
    key(name, True)


def mouse(dx, dy=0):
    i = INPUT(type=0)
    i.mi = MOUSEINPUT(int(dx), int(dy), 0, 0x0001, 0, 0)
    _send(i)


def wheel(ticks):
    """+ up (faster / previous entry), - down (slower / next entry)."""
    for _ in range(abs(int(ticks))):
        i = INPUT(type=0)
        i.mi = MOUSEINPUT(0, 0, (120 if ticks > 0 else -120) & 0xFFFFFFFF, 0x0800, 0, 0)
        _send(i)
        time.sleep(0.03)


def click(button, hold=0.08):
    down, up = button
    for flag in (down, up):
        i = INPUT(type=0)
        i.mi = MOUSEINPUT(0, 0, 0, flag, 0, 0)
        _send(i)
        time.sleep(hold)
