# Sam Fisher bot - training set and playbook

Everything the bot (Claude driving Sam through `tools/bot/sam.py`) learns while playing Splinter Cell:
Chaos Theory, plus the recorded runs. Read this before the next session.

Runs (every mission played, human or bot) are recorded by OPSAT into
`%USERPROFILE%\Saved Games\OPSAT\runs\<mission>_<date>_<player>.jsonl`: one line per 0.5 s with room,
Sam (x, y, z, camera yaw), alarm stage and the guards within 30 m (x, y, z, yaw, mood 0 calm, 1 suspicious,
2 alert, 3 out, 4 dead), plus a line whenever objectives change. Samples taken while the bot drives carry
`"b":1` (the controller touches `runs/bot_active`; files opened while it drives end in `_bot`). Curated
copies live in `training/runs/` - one file per mission session.

Tools (`tools/bot/`, run from that folder with the game in front): `sam.py look | goto X Y | walk M |
turn DEG | face X Y | key NAME | speed N | route X,Y X,Y ...`, `topdown.py NAME [radius]` (map of rooms,
guards, beacons and recorded tracks, up = camera forward), `probe.py [m]` (tries 9 headings, reports which
way is open), `mkey.py` / `typekeys.py` (menus, save names), `live.py shot NAME` (screenshot + OPSAT).

## Controls that work (from a separate process)

- Movement and game actions need **scancode SendInput** (`KEYEVENTF_SCANCODE`). Virtual-key `keybd_event`
  is ignored by the game but is seen by OPSAT's hotkeys (GetAsyncKeyState) and by menus.
- Bindings (profile DEFAULT): W/A/S/D move, **C = crouch toggle**, Space = interact, Shift = jump,
  mouse = look, **mouse wheel = movement speed** (down = slower, quieter), LMB fire, RMB alt-fire.
- Camera: relative `MOUSEEVENTF_MOVE`; 15.3 counts per degree of yaw (calibrated, `sam_cal.json`).
- Memory: `Pawn.bIsCrouched` = pawn + 696 bit 2; `Actor.LuminosityFactor` = pawn + 612 (light level:
  0-4 in shadow, 35 under a lamp, 67 in front of a lit facade); `Pawn.GroundSpeed` = pawn + 744.
- Pressing S makes Sam turn around and walk toward the camera.

## Menus

- Menus take W/S (scancode) to move and Enter to select. Menu focus also follows the mouse hover, and the
  pause menu **remembers the last item** - always clamp first (W x7 = top) and **screenshot before Enter**.
- Wait ~2 s after Esc before sending menu keys; early keys go to Sam and Enter hits a remembered item
  (that is how "Quit to main menu?" and an accidental junk save "BANKwwww..." happened).
- Pause menu: SAVE GAME, LOAD GAME, RESTART MISSION, SETTINGS, TRAINING VIDEOS, QUIT TO MAIN MENU.
  Game-over menu: CONTINUE (disabled), LOAD GAME, RESTART MISSION, QUIT. Confirm dialogs default to NO
  (W then Enter = YES).
- Save screen: click the name field first (focus is otherwise on the list, where Enter = overwrite).
  Names over ~16 characters get mangled: keep them short (LIGHTHOUSE_BOT, BANK_BOT_START).
- User rule: when the run is blown (guard alert / alarm), Esc and reload the last checkpoint. Save with a
  clear name after every major checkpoint. Focus on playing, not menus.

## Stealth mechanics learned the hard way

1. **Light decides everything.** Crouched in shadow (light < 10) guards walked past at 3.6 m without
   noticing. In light (35+) a guard at 12-13 m spots Sam within seconds, even crouched.
2. Never "freeze" while lit and watched - back off to the last dark spot. Holding still is only safe in
   shadow.
3. Crouch can drop (stairs, ledges, interactions): re-check `bIsCrouched` every second.
4. **Breakers bring a guard.** After switching off a breaker a guard comes to check it, with a flashlight.
   Do not retreat along his path; wait deeper in the dead end or get behind him.
5. Bank courtyard (03_Bank, Front_Garden): the lit front of the bank is lethal; the right side near the
   start is lit too (run 2 died there in 7 m). The proven dark line: start (4938, 7443) -> (4037, 7304) ->
   (2956, 7539) -> palm corner (2020, 7570), light 0-1.2 the whole way. The left alley from the palm corner
   (1051, 7721) -> (368, 7775) is dark; the courtyard **breaker** is on the bank wall at the alley's end,
   about (330, 7530): face it, Space = "Switch object". The goal (Bank_Side) is the right alley with the
   ladder to the roof.
6. The talking pair and a patrolling walker work the Bank courtyard; the walker loops past the start.

7. **Use OPSAT first.** INTEL's FROM HERE gives the area's goal and the steps; read it before moving
   (Lighthouse beach: "natural ramp on the far left between the lamp post and the wooden debris").

## Mission notes (bot routes that worked)

**01 Lighthouse, Beach -> Cavern.** Start (4578, 2253, -698) by the boat, facing the sea; the cliff is to
the left. Crouched, dark the whole way: (4612, 1599) wooden crates, lamp post ahead-right (light 16 at its
edge) -> (4790, 1179) on the rock ramp (z rises) -> (4956, 854) light 0 -> crevice mouth (5339, 579, -553).
A plain forward move stops there: the narrow passage bends; next session run `probe.py` from the crevice
mouth, then the crawlspace (crouch) leads to the Cavern (room centre about (4967, -1305)).

## Run log

| # | Mission | Result | What happened |
|---|---------|--------|---------------|
| 1 | Bank | caught | Crossed the lit front (light 67); froze while a guard looked; 4 alert, mission failed |
| 2 | Bank | caught | Went right from the start into a lit patch (35); guard at 13 m alerted, killed |
| 3 | Bank | caught | Dark route + breaker worked; a guard came to check the breaker with a flashlight and met Sam in the alley |
| 4 | Lighthouse | in progress | New campaign on Normal (save LIGHTHOUSE_BOT). Beach ramp climbed unseen, at the crevice mouth when the game closed (22:43) |
