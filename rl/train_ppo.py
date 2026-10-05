"""PPO from scratch (self-play, no demonstrations). Checkpoints every 5k steps to rl/runs/.
Real time at 4 steps/s: 100k steps is about 7 hours of play, so start with short episodes."""
import os
import sys
import gymnasium as gym
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import CheckpointCallback
from stable_baselines3.common.monitor import Monitor
import scct  # noqa: F401

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'runs')
steps = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
env = Monitor(gym.make('SCCTNav-v0', max_steps=600), OUT)
resume = os.path.join(OUT, 'latest.zip')
model = PPO.load(resume, env) if os.path.exists(resume) else PPO(
    'MlpPolicy', env, n_steps=512, batch_size=128, gamma=0.995, gae_lambda=0.95, ent_coef=0.01,
    learning_rate=3e-4, verbose=1, tensorboard_log=OUT)
try:
    model.learn(steps, callback=CheckpointCallback(5000, OUT, 'ppo'), reset_num_timesteps=False)
finally:
    model.save(resume)
    env.close()
