"""Smoke test with the game running in front: space checks, then random steps through at least one reset.
(Gymnasium's check_env is not used: it requires deterministic steps, which a live real-time game can't give.)"""
import time
import numpy as np
import gymnasium as gym
import scct  # noqa: F401

env = gym.make('SCCTNav-v0', max_steps=40)
obs, info = env.reset()
assert env.observation_space.contains(obs), obs
print('reset ok', info, 'obs', np.round(obs, 2))
for ep in range(2):
    total, t0 = 0.0, time.monotonic()
    for t in range(40):
        obs, r, term, trunc, info = env.step(env.action_space.sample())
        assert env.observation_space.contains(obs) and np.isfinite(r)
        total += r
        if term or trunc:
            break
    print('episode %d: %d steps, return %.2f, %.1f steps/s, end %s' % (
        ep, t + 1, total, (t + 1) / (time.monotonic() - t0), info.get('end')))
    obs, info = env.reset()
    print('reset ok', info)
env.close()
