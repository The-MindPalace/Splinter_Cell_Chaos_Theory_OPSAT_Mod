# SCCT as a reinforcement-learning environment

Goal: an agent that learns Splinter Cell: Chaos Theory by self-play and finds the best path through it.
First target: finish mission 1, Lighthouse. This folder is the smallest environment that can start that.

## SCCTNav-v0 (built)

**Task.** Get to the next objective without being detected. The goal is the game's own: the next pending
objective beacon on the 3D map, routed through the map's room graph (room centres as waypoints, then the
beacon). No human routes or demonstrations go in; recorded runs are only used for evaluation.

**Interface.** Gymnasium `Env`, real time, 4 steps per second (each action held 0.25 s).

| | |
|---|---|
| Observation (34 floats) | goal in camera frame (right/forward/up, distance, heading sin/cos), rooms left on the route, light level, crouched, speed, blocked, alarm, health, time left; 4 nearest guards (camera-frame position, height, facing Sam, mood) |
| Action | MultiDiscrete: move (none, W, S, A, D, W+A, W+D) x turn (-30, -10, 0, +10, +30 deg) x crouch toggle x (none, jump, interact) |
| Reward | +1 per metre of route progress (potential-based, so it cannot be farmed), +10 per route room entered, +100 objective done, -0.01 per step, -0.02 x light while a guard within 15 m faces Sam, -5 when a guard turns suspicious, -100 detected, -100 dead |
| Terminated | objective completed (success), detected (alert within 40 m or alarm rises), dead / mission over |
| Truncated | step limit, or blocked for 40 steps |
| Reset | F8 quickload of the stage quicksave; the first reset makes that quicksave with F5 wherever Sam stands |

**Telemetry** comes from OPSAT's reader (`CheatOverlay/cheat_overlay.py`), read-only: Sam pose and camera
yaw, crouch, light level (`Actor.LuminosityFactor`), health, guards (pose, view cone, mood), alarm stage,
objectives, 3D-map beacons, room graph.

## Run it

```
pip install -r rl/requirements.txt
cd rl
python smoke.py            # game running, in front, mission loaded where the episode should start
python train_ppo.py 20000  # PPO from scratch; checkpoints in rl/runs, resumes from latest.zip
```

The desktop must stay unlocked with the game window in front: Windows blocks synthetic input and screen
capture on a locked or sleeping desktop.

## Known limits and next steps

1. **Sample cost.** Real time is the bottleneck: 100k steps is about 7 hours. Plans: curriculum of short
   stages (beach -> cavern -> cellar ...), each a quicksave; then game-speed control by writing
   `LevelInfo.TimeDilation` (a memory write - opt-in, outside OPSAT, which stays read-only).
2. **No geometry.** Memory gives no walls. The agent learns collisions from the `blocked` flag; the next
   observation upgrade is a small grayscale frame (84x84) for a CNN policy, which the caves will need.
3. **Interactions.** Objectives that need a context action (doors, switches, rescuing Morgenholt) depend on
   the interact action at the right spot; a later version reads the game's interaction prompt from memory.
4. **Detection = episode end** for now (pure ghost). Later: allow knockouts and score by the game's stealth
   rating.
5. **Mission-failed screens.** A death ends the episode; reset quickloads from the game-over screen. If F8
   does not work there, add the menu path (Load Game -> newest save) from `tools/bot`.
