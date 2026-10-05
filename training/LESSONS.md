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

**01 Lighthouse, Cavern -> Entrance floor (found 2026-10-05 with night vision, tools/bot/drive.py).**
From the deep Cavern (5010, -309, -167) go west through the low tunnel toward the light (it bends left)
into a lit chamber (4289, -293, light 130+). Face south: the HUD says INTERACT - ENTER CRAWL SPACE at
(4286, -346). Space to enter, walk, Space at EXIT FRONT (4309, -563) - the scripted Fisher/Lambert talk and
the objective "discover where the guerillas got their arms" start here. Then four ledge climbs heading south
past the small waterfall (+86, +195, +91, +55 cm) to (4360, -1009, 245); a rope bridge is off to the right.
Two metres on, Sam is on the guards' nav mesh (Entrance floor). The bot replays all of this alone now.

**01 Lighthouse, beyond.** 3D-map room route to Morgenholt: Cavern -> Wine Cellar (5386, -3693, 344; on the
Entrance floor mesh, 42 waypoints from the bridge area) -> Ampitheatre (7204, -4494, 796) -> Torture
Dungeon (7358, -3768, 819), both on the Dungeon floor ~5.5 m higher. The way up between them is not in the
mesh (probably stairs); the bot explores it from the Entrance floor's edge nearest the Ampitheatre.
Two guards chat near (5404, -2626) at the end of the bridge area.
**Found 2026-10-05 23:20:** the way up is a long staircase leaving the Wine Cellar's east doorway at about
(5650, -3930, z 307), next to the cells: first flight to a landing at z 510 (x 6065-6432), second flight to
z 850-917 at (6920-7012, -3988) - the top of the Ampitheatre ramp. The bot circled the doorway for 100+
decisions without trying it (its experiments were blocked by the door frame); walking straight east does it.
From the top: "Get down into the torture chamber unseen" (Morgenholt at (7911, -4073, 649)).

## Level knowledge the bot reads from memory

- **AI nav mesh** (rl/scct/navmesh.py): ENavMesh objects, verts at +0x50, 60-byte triangles at +0x68
  (3 vertex indices, 3 neighbours, centre). Lighthouse: 1563 triangles, 9 areas. Guards' ground only - no
  Sam-only routes (crawlspaces, ledges, pipes). Closest border points between floors are often sheer walls,
  so floors are joined by exploring, not by the mesh's geometry.
- **3D map rooms**: E3DMapSystem room graph + zone points per room (EZoneInfo actors) - the semantic route.
- **Interactions**: blocked + nothing near -> Space once (crawlspaces/doors). NEVER with a guard within 3 m:
  Space grabs him (the bot once walked a guard around for ten minutes).
- **Cheats for mapping**: F2 god mode, F3 invisible (verified in memory). Map geometry learned with cheats
  counts; stealth style learned with cheats does not.
- **Pawn z is the body centre**: about 70-90 cm above the floor (mesh z 256 floor = Sam z ~330).

## Run log

| # | Mission | Result | What happened |
|---|---------|--------|---------------|
| 1 | Bank | caught | Crossed the lit front (light 67); froze while a guard looked; 4 alert, mission failed |
| 2 | Bank | caught | Went right from the start into a lit patch (35); guard at 13 m alerted, killed |
| 3 | Bank | caught | Dark route + breaker worked; a guard came to check the breaker with a flashlight and met Sam in the alley |
| 4 | Lighthouse | in progress | New campaign on Normal (save LIGHTHOUSE_BOT). Beach ramp climbed unseen, at the crevice mouth when the game closed (22:43) |
| 5 | Lighthouse (RL bot, map mode) | Entrance floor | 2026-10-05: explore map seeded from recordings; stuck under the Cavern ledge until the crawlspace was found by hand; then beach -> Entrance floor -> bridge guards alone; a blocked-Space grabbed a guard (fixed) |
