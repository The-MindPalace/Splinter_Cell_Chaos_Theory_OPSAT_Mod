# SCCT as a reinforcement-learning environment

Goal: an agent that learns Splinter Cell: Chaos Theory by self-play and finds the best path through it.
First target: finish mission 1, Lighthouse. This folder is the smallest environment that can start that.

## SCCTNav-v0 (built)

**Task.** Get to the next objective without being detected. The goal is the game's own: the next pending
objective beacon on the 3D map, routed through the map's room graph (room centres as waypoints, then the
beacon). No human routes or demonstrations go in; recorded runs are only used for evaluation.

**Interface.** Gymnasium `Env`, real time: each action held 0.25 s, about 3 steps per second with the memory
read. Verified 2026-10-05 in Lighthouse: smoke test passes, F8 reset returns Sam to the exact start.

| | |
|---|---|
| Observation | `img`: the game window as 84x84 grayscale (what Sam sees; OPSAT's overlay is excluded), read by a CNN; `vec`: 34 floats - goal in camera frame (right/forward/up, distance, heading sin/cos), rooms left on the route, light level, crouched, speed, blocked, alarm, health, time left; 4 nearest guards (camera-frame position, height, facing Sam, mood) |
| Action | MultiDiscrete: move (none, W, S, A, D, W+A, W+D) x turn (-30, -10, 0, +10, +30 deg) x crouch toggle. Jump/interact only with `full_actions=True` (off for navigation, so no hopping) |
| Reward | +1 per metre of route progress (potential-based, so it cannot be farmed), +10 per route room entered, +100 objective done, -0.01 per step, -0.02 x light while a guard within 15 m faces Sam, -5 when a guard turns suspicious, -100 detected, -100 dead |
| Terminated | objective completed (success), detected (alert within 40 m or alarm rises), dead / mission over |
| Truncated | step limit, or blocked for 40 steps |
| Reset | F8 quickload of the stage quicksave; the first reset makes that quicksave with F5 wherever Sam stands |

**Telemetry** comes from OPSAT's reader (`CheatOverlay/cheat_overlay.py`), read-only: Sam pose and camera
yaw, crouch, light level (`Actor.LuminosityFactor`), health, guards (pose, view cone, mood), alarm stage,
objectives, 3D-map beacons, room graph.

## SCCTFisher-v0 (built): the player's playbook + a learner that picks moves

Raw keys meant thousands of random steps before Sam walked in a line. The house style is now code
(`scct/skills.py`, controls manual in `scct/controls.py`) and the agent only chooses which skill to run:

| Option | What the playbook does |
|---|---|
| ADVANCE | crouched stealth-walk to the next route waypoint; speed from the threat level (15% clear, 8% guard within 20 m, 4% within 10 m or watched, 2% next to one; never 0); path offset to pass behind a guard instead of through his cone; stuck -> climb only if the waypoint is above, else sidestep |
| WAIT | hold still, crouched |
| HIDE | nearest dark spot (from Sam's own memory of where light was <= 5) away from guards, then wait until calm |
| TAKEDOWN | calm guard not looking -> walk to 1.1 m behind him (re-planned as he moves, abort if he turns), Space = grab, interrogate if the interaction list offers it, right mouse = knock out, verified by his AI state |
| DUMP | pick a spot: dark, no guard within 15 m, no guard walked within 10 m or had it in his cone in the last 5 minutes, not a spot that failed before; carry, drop, then keep auditing - a guard looking at the body marks the spot bad for every later run |
| SIDESTEP L/R | strafe around an obstacle |

Emergency reflexes (not learned): suspicious -> freeze if in shadow, hide if lit, take him if he passes
within 2.5 m with his back turned; alert but not looking -> break line of sight and wait 20 s; alert,
looking, within 6 m -> close in and strike (right mouse); alarm or two alert -> episode over (reload).
Bodies found -> hide.

Memory per mission (`Saved Games/OPSAT/runs/fisher_memory_<mission>.json`): dark spots, each guard's track
(position and facing once a second), bad body spots. It grows every run.

Run the playbook alone first (baseline, and a live test of every skill), then train on top:
```
python rl/fisher.py 200        # playbook only, prints every decision
python rl/train_ppo.py 5000    # PPO picks the options (SCCTFisher-v0 is the default)
```

To verify on the first live run: the wheel-speed calibration (does Pawn.GroundSpeed follow the wheel; else
20 steps assumed), the interaction-list reader (`prompt_rows`, HUD box position), right mouse = knock-out
while holding a guard, Space = pick up / drop body.

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
2. **No geometry in memory.** Walls come from the 84x84 view (CNN) plus the `blocked` flag.
3. **Interactions.** Objectives that need a context action (doors, switches, rescuing Morgenholt) depend on
   the interact action at the right spot; a later version reads the game's interaction prompt from memory.
4. **Detection = episode end** for now (pure ghost). Later: allow knockouts and score by the game's stealth
   rating.
5. **Mission-failed screens.** A death ends the episode; reset quickloads from the game-over screen. If F8
   does not work there, add the menu path (Load Game -> newest save) from `tools/bot`.
