"""Smoke test with the game running in front: gymnasium's env checker, then 40 random steps."""
import gymnasium as gym
from gymnasium.utils.env_checker import check_env
import scct  # noqa: F401

env = gym.make('SCCTNav-v0', max_steps=60)
check_env(env.unwrapped, skip_render_check=True)
obs, info = env.reset()
print('reset ok', info, obs.shape)
total = 0.0
for t in range(40):
    obs, r, term, trunc, info = env.step(env.action_space.sample())
    total += r
    if term or trunc:
        print('episode end', info)
        break
print('return %.2f over %d steps' % (total, t + 1))
env.close()
