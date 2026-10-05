"""Run Fisher's playbook alone (no learning) through the mission from where Sam stands: the baseline to beat,
and a live test of every skill. Game in front, mission loaded. Ctrl+C stops (keys are released)."""
import sys
import gymnasium as gym
import scct  # noqa: F401
from scct.options_env import OPTIONS, baseline_policy

decisions = int(sys.argv[1]) if len(sys.argv) > 1 else 200
env = gym.make('SCCTFisher-v0', max_decisions=decisions).unwrapped
obs, info = env.reset()
print('start', info)
total = 0.0
try:
    for n in range(decisions):
        a = baseline_policy(env)
        obs, r, term, trunc, info = env.step(a)
        total += r
        print('%3d %-10s %-32s r %+7.2f  total %+8.2f  room %s' % (n, OPTIONS[a], info['result'][:32], r, total,
                                                                  info['room']))
        if term or trunc:
            print('end:', info.get('end', 'time'))
            break
finally:
    env.fisher.save()
    env.close()
