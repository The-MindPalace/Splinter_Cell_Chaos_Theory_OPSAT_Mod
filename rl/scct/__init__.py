"""Splinter Cell Chaos Theory as a Gymnasium environment (telemetry via OPSAT's memory reader)."""
from gymnasium.envs.registration import register

register(id='SCCTNav-v0', entry_point='scct.env:SCCTNavEnv')
