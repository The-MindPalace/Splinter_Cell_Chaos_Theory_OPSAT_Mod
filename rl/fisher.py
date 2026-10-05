"""Run Fisher's playbook (no learning) episode after episode: the baseline to beat, and the explorer that
maps the level. Unattended-safe:
  * every episode end (objective, detected, dead, time) -> quickload and go again
  * reset fails / game crashed -> restart the game and CONTINUE from the latest save
  * keeps the PC awake while it runs (process-level request, released on exit)
  * per-episode summary lines in rl/runs/fisher_episodes.jsonl, every decision on stdout

  python rl/fisher.py                 # one episode, 200 decisions
  python rl/fisher.py --hours 6       # unattended: episodes for up to 6 hours
"""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time

import gymnasium as gym
import scct  # noqa: F401
from scct.controls import tap
from scct.game import co
from scct.options_env import OPTIONS, baseline_policy
from scct import launcher

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'runs')
ES_CONTINUOUS, ES_SYSTEM, ES_DISPLAY = 0x80000000, 0x00000001, 0x00000002


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def wait_live(timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            pid = co.find_pid()
            if pid:
                g = co.Game(co.Mem(pid))
                mission, room, objs = g.mission_state()
                if mission not in (None, 'menu') and objs and room:
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


def relaunch():
    """Game crashed or hung: kill what is hung, launch through Steam, menus -> CONTINUE (latest save).
    All the care (popups, hung processes, looking before each key) lives in scct/launcher.py."""
    log('recovery: restarting the game')
    subprocess.run(['taskkill', '/F', '/IM', 'splintercell3.exe'], capture_output=True)
    time.sleep(5)
    ok = launcher.launch()
    log('recovery:', 'back in the mission' if ok else 'FAILED')
    return ok


def run_episode(env, decisions, ep):
    obs, info = env.reset()
    log('episode %d start %s' % (ep, info))
    total, end, n = 0.0, 'time', 0
    for n in range(decisions):
        a = baseline_policy(env)
        obs, r, term, trunc, info = env.step(a)
        total += r
        log('%3d %-10s %-40s r %+7.2f total %+8.2f room %s' % (n, OPTIONS[a], info['result'][:40], r, total,
                                                               info['room']))
        if term or trunc:
            end = info.get('end', 'time')
            break
    x = env.fisher.explorer()
    rec = {'t': time.strftime('%Y-%m-%d %H:%M:%S'), 'episode': ep, 'decisions': n + 1, 'return': round(total, 2),
           'end': end, 'room': info.get('room'), 'map': x.stats()}
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'fisher_episodes.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec) + '\n')
    log('episode %d end: %s' % (ep, json.dumps(rec)))
    env.fisher.save()
    x.save()
    return rec


def single_instance():
    """Two bots fighting over Sam corrupt the run and the exploration map: refuse to start a second one.
    The lock is held for the life of the process (released by Windows when it exits, even if killed)."""
    import msvcrt
    os.makedirs(OUT, exist_ok=True)
    f = open(os.path.join(OUT, 'fisher.lock'), 'a+')
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        sys.exit('another fisher.py is already running (python rl/stop_bot.py stops it)')
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hours', type=float, default=0, help='unattended: keep running episodes this long')
    ap.add_argument('--decisions', type=int, default=200, help='decisions per episode')
    ap.add_argument('--map', action='store_true', help='mapping: God mode + Invisible on, full crouched pace')
    ap.add_argument('--steal-focus', action='store_true', help='bring the game back to the front (nobody at the PC)')
    args = ap.parse_args()
    lock = single_instance()  # noqa: F841 (held until exit)
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM | ES_DISPLAY)
    deadline = time.time() + args.hours * 3600
    ep, env, failures = 0, None, 0
    try:
        while True:
            try:
                if env is None:
                    env = gym.make('SCCTFisher-v0', max_decisions=args.decisions).unwrapped
                    env.unattended = args.steal_focus
                    env.map_mode = env.fisher.fast = args.map
                run_episode(env, args.decisions, ep)
                ep, failures = ep + 1, 0
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log('error:', repr(e))
                failures += 1
                try:
                    if env:
                        env.close()
                except Exception:
                    pass
                env = None
                if not args.hours or failures > 4:
                    raise
                if not relaunch():
                    time.sleep(60)
            if not args.hours or time.time() > deadline:
                break
    finally:
        if env:
            env.fisher.save()
            env.close()
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


if __name__ == '__main__':
    main()
