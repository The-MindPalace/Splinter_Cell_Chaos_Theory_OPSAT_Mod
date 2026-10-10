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
At the top of the stairs a closed DOOR (EDoorInteraction47, (7268, -4280)) blocks the mesh path south:
INTERACT offers Open door / Open door stealth / Bash door / Optic cable. One wheel notch down then Space =
open stealthily (the bot does this now when stuck near a door). Then the ramp runs south and down
(z 883 -> 560) and back north into the torture chamber (7700, -3900, z 622). Reaching Morgenholt fails
"Rescue Morgenholt" by script - he cannot be saved (the notes say so); the mission moves on to the
courtyards ("leave through the alcove behind the big floodlight, then pick the courtyard door lock").
Scanning a camo crate in the Wine Cellar (Space next to it) added the objective "Scan the SSCC bar code".
Takedowns that time out twice on the same guard are dropped; no takedowns in no-fight rooms or map mode.

**Courtyards (2026-10-06 ~01:00).** Torture room exit door EDoorInteraction46 (6478, -3636) is LOCKED:
Space opens the PICK LOCK view; solved by lockpick.py (tap the 8 directions, the one that shakes the pins
is held ~3.5 s to lift a pin; the view closes when all pins are up; Esc leaves it). Then Front Courtyard
(checkpointed), Rear Courtyard (checkpointed); crates scanned on the way: ECustomObjectiveInteraction8, 10,
16. Southwest Tower floor (server, Masse Kernels objective) is x 1121..1680, y -2162..-1285, z 1217 - 3.7 m
above the courtyard (z 775-849) and ~5 m from its NW corner (1828, -2379). Notes: crawlspace behind the
floodlight (+2 more counter-clockwise), or climb the pallets by the right-hand tent's crate (works: the crate
stack at (2142, -2317) climbs to a raised passage z 922-961 running to a nook at (1588, -2547)), or the low
way: doorway under the bright light, past the tin-roof gazebo, jump onto the roof. Not solved yet.
Climb surveys found climbs from (20, -26) heading 0 (+224 cm, onto the roof at z ~1130) and from (22, -23)
heading 270 (+158 cm); Sam reached z 1263 at (1949, -2070), ~3 m short of the tower floor's east edge (x 1680).
A progress checkpoint is saved up there ("near Southwest Tower").

**04 Penthouse - Pranav's blind run (2026-10-08).** Reviewed frame by frame: 15 min of video (from the Helipad
on) and the run log (all 32 min). Times are from each attempt's run log. Per area: what OPSAT INTEL said, what
he did, and why.

- **Alley** (run log only, before the video started).
  - *Route:* OPSAT's high road, exactly. Crates at (11005, -1091) (+2.3 m, +1.65 m), up the fire escape to
    z 1027, 9 m north along the ledge, down the drainpipe at (10530, 3).
  - *The yard below:* 4-5 National Guard around (10450..11050, 30..1550). **Both straight attempts were
    spotted here.**
    1. He crept up behind a guard (130 deg off his facing) 6 s after the yard had calmed down from suspicious.
       The guard turned at 0.9 m.
    2. Three guards went suspicious at 5-8 m in light 8.
  - *Gap in OPSAT:* its tips stop at the drainpipe. The yard itself has none (the walkthrough's street option,
    shoot the big spotlight and OCP the second, was not tried).
  - *Yard route used in the end (geometry):* (10474, 550) -> (10904, 753) -> (10825, 1094) wait ->
    (11009, 1209) -> (11105, 1602) -> (10780, 1839) -> (10476, 1539) -> (10044, 1378, z 62) -> Corridor.
- **Corridor and shaft.** Grabbed the guard at (9374, 2219) from behind. Skipped the elevator and climbed the
  shaft ladder (the walkthrough's "long ladder") from (9295, 2524) to the top, z ~5036, in about 30 s.
- **Helipad, attempt 1.**
  - Read INTEL for 30 s in the dark by the pipes.
  - Used the light switch (SWITCH OBJECT, 21:20). A guard came to inspect the box. Sam waited 3.4 m behind him
    for 25 s while he faced it, grabbed him (21:52), interrogated him, knocked him out and left the body in the
    dark.
  - Then he stood up and walked into the helicopter spotlight (22:24, light 83 -> 213). The two pad guards
    (15 m) went suspicious, then alert: reload.
- **Helipad, attempt 2 - the adaptation.**
  1. The same switch (00:26), now used as a **decoy**: the three pad guards went suspicious and turned toward
     its dark corner (00:36).
  2. He went the other way, round the dark west side behind the ramp and crates (00:48-01:12), and waited 22 s.
  3. He **OCP'd the floodlight** (01:04; the laser is red while the OCP recharges; again at 01:12) and crossed
     during the blackout (01:13-01:20).
  4. At the base of the sign he re-read INTEL (01:28-01:38) and climbed when the guards turned back (01:40).
  5. The sign walkway is bright (light 130-550). The pad guards saw him at 12 m (alert 01:48), but he was
     already on the zip-line. Objective done 01:52; **saved straight away** (02:06).
  - *OPSAT:* FROM HERE suggested the passive plan (pipes, dark structure, wait for the talk), which he never
    used. Everything that worked (switch, floodlight OCP) was in FOR THE OBJECTIVE. The one step there he
    skipped, "OCP the AXE sign", is the one that got him seen.
- **Balcony.**
  - **OCP'd the walkway lamps first** (02:24-02:27: the OCP bar drains, the lamps die, light 33 -> 12).
  - Waited in a dark corner while the flashlight beam swept past at 1.8 m (02:52-03:04). Grabbed him from
    behind as he passed (03:06).
  - **Dragged him about 9 m into total dark** (light 0) before interrogating (03:20-03:54): who the mercs work
    for. Left the body there.
  - *OPSAT:* FROM HERE showed three ways to sneak past. The tip he actually used ("knock him out; he tells you
    who the mercs work for") is 4th in hints.json and the panel shows only 3.
- **Construction Floor.**
  - Behind the cabinet (FROM HERE tip 1), then the **high crawl space** (04:44). The crawl space is only in the
    floor-plans objective notes, not in FROM HERE. Out by the plastic sheet.
  - OCP'd lights at 05:06 and 05:32.
  - **The alert, and why:** right after the second jam he hurried across in the dark at ~2 m/s, because a jam
    only lasts seconds. The **noise meter** (the bar above the weapon) filled to 5 segments at 05:36-05:37. The
    guard 6.6 m away, walking toward him, went alert at 05:37.0; two more followed.
  - He then picked the locked door in the dark while they searched (05:44-05:56) and took the fire-escape
    stairs.
- **Guest Room (music room).**
  - OCP'd the lamp over the door; he waited ~10 s with the red laser on it until the charge came back.
  - Inside, the room went dark (switch by the door) and the seated guard got up to search (06:50). Sam stayed
    still, followed him at 4-5 m and grabbed him from behind (07:24).
  - Dragged him into the dark corner and interrogated him (07:28-08:06), then knocked him out.
  - Out through the patio (OCP'd a light, switch by the glass door) into the Kitchen.
- **Kitchen.**
  - Watched from the dark side (08:54-09:02), selected the Sticky Shocker (09:10), climbed the island counter
    and went along the wall to the fern (OPSAT tip). Nobody noticed.
  - Aimed at the patroller standing alone at (5221, 162) and pressed **left click: the SC-20K fired a 7-round
    burst** (30/60 -> 23/53, 09:36-09:38). Every guard on the floor went alert (6 -> 9); he quit.
  - **Sticky shocker / launcher = right click (AltFire); middle click = scope; left click = bullets.**

**His playbook, as the bot should copy it:**
1. Read INTEL in the dark before each area, then act on it, re-reading at decision points (sign base).
2. **Kill the light before moving.** OCP the lamp or floodlight on the path; if the laser is red, wait for the
   charge rather than going without it.
3. **Use switches as decoys.** Guards walk to a switched-off light. Either go the other way (Helipad 2) or take
   the guard who comes to check it (Helipad 1, Guest Room).
4. **Takedowns from behind**, timed for when the guard walks past or faces something (the switch box). Always
   drag him into full dark before interrogating. Leave the body in the dark.
5. Save right after a hard crossing.
6. Adapt between attempts: the same switch was a takedown opportunity the first time and a decoy the second,
   once the first plan led into the spotlight.

**What actually caused each detection (darkness never failed on its own):**
- Standing up in a spotlight (Helipad 1).
- The lit sign walkway without jamming it (Helipad 2; no harm, already zipping).
- Hurrying through an OCP window: noise (Construction Floor).
- Coming up behind a guard who had just calmed down (Alley).
- The wrong fire button (Kitchen).

**Bot rules from this:**
- Jam before crossing a lit stretch. Cross a jam at the slowest crouch; if the window is too short, re-jam
  instead of running.
- Never stand in light except for the grab.
- Interrogate only at light < 5.
- Non-lethal shots use AltFire.
- Spotlit open ground is crossed only during a blackout.
- No approach within 20 s of a guard calming down.

**OPSAT INTEL findings (from this run):**
- The panel shows only 3 FROM HERE tips, and FOR THE OBJECTIVE is squeezed to about 1.
- Where his route differed (Helipad, Balcony, Construction Floor), the tips that worked were hidden or sat in
  the small objective block.
- The Alley yard has no tips at all.
- INTEL lagged a room at the Construction Floor door (04:16-04:26 still showed the Balcony).

**04 Penthouse - Pranav, second day (2026-10-10).** Three sittings (14:18, 16:08, 16:27). The last one is on
video with every key and mouse button (inputs.csv); the first two are log-only.

**Helipad practice: 13 attempts, all from the Helipad Storage save.**
- *Watchers:* the sign ladder and its approach (7737..8179, 963..1143) are lit (47-143). Three guards cover it:
  the patroller on the y ~1610 line, (9028, 1262), and (8188, 1609).
- *Failures, all variations of being lit in their view:*
  - stepping out at light 42-52 within 2-6 m of the patroller;
  - climbing the ladder standing at light 53-143 while they faced it;
  - (8179, 963) at light 97, 6.9 m from a guard.
- *New hiding spot he found:* a dark nook at (8462, 620-720, z 5136), light 0, guards 7-10 m. He returned to it
  in every later attempt and waited 40-80 s there.
- *One attempt took the patroller from behind* as he passed (8736, 1609) and carried him 11 m to the nook. The
  next patroller passed ~7.6 m from the body 40 s later and went alert with a second guard. A body dropped at the
  nook mouth is not hidden.
- *Video attempt 1:*
  1. Hopped the pipes (Shift x3, OPSAT tip 1).
  2. Selected the Sticky Shocker (quick inventory, 00:32).
  3. Scoped the two pad guards at 22 m, cycling vision modes with the number keys (00:44-01:12).
  4. Crept to 7 m from the guard under the wall lamp and pressed **left click: one rifle round**
     (30/60 -> 29/59, 01:32.5). Noise meter spike, three guards alert at 01:33.0. The right clicks that followed
     (01:33.5, 01:35) did fire shockers (3 -> 1), too late.
  - **The same slip as the 10-08 kitchen: with the SC-20K out, left click is the rifle even when the shocker
    is selected. Right click fires the attachment.**
- *The crossing that worked (video, 02:46-03:22):*
  1. The pad guards were already pulled east, suspicious since 02:36, when the corridor lamp by the switch box
     went dark.
  2. He scoped the floodlight; the laser stayed red until the OCP had charged. Jammed it at 03:04 and crossed
     during the blackout (03:06-03:12).
  3. Up the ladder (03:14) and over the lit sign (light 173) while the three guards were 18-20 m away, facing
     the other way.
  4. Zip-line; objective done 03:23.
  - **The same lit sign that got him seen on 10-08 and in three attempts today is safe once the guards have been
    drawn off and are looking away.**

**Penthouse floors (video).**
- **Balcony.**
  - *First try:* grabbed the flashlight guard, then pressed right click (knock out) before interrogating. That
    loses "who the mercs work for", so he loaded his PENTHOUSE_HELICOPTER_PASSED save.
  - *Second try:* grab (04:36), Space repeatedly until the interrogation ends (04:40-04:56), then right click
    (04:58). Objective done. **Held guard: Space = interrogate, right click = knock out. Always interrogate
    first.**
- **Construction Floor, clean.**
  - Cabinet, then the crawl space (Space, 05:30; EXIT at 05:44).
  - He walked past the napping guard at 3-4 m at a crouch (06:00-06:08), picked the door (06:14-06:28) and
    jammed the stair light (06:36-06:40).
  - Two guards went suspicious while he was in the crawl space and calmed down. No alert: **on 10-08 the alert
    came from jamming the hall light and then hurrying; this time no jam there, no hurry.**
- **Guest Room, no takedown.**
  - He jammed the room lamp with the seated guard 3.3 m away (07:06-07:08). The guard got up to search; Sam went
    to night vision and slipped past behind him along the wall (07:10-07:22).
  - Jammed the patio light and went out into the Kitchen. This is OPSAT's tip, done exactly.
- **Kitchen.**
  - *Two takedowns,* both from behind in the dark on moving guards: a searching guard (07:58), then a patroller
    (08:36). Each was dragged to light 0 first.
  - *The second guard's interrogation* (08:42-09:16) **completed "Discover who is in charge of the protection
    detail" (08:54)**. OPSAT's data says the greenhouse guard upstairs gives it.
  - Then the hallway by the fern, where the 10-08 run ended: no shot this time.
- **Living Room.**
  - Jammed light after light (scope, right click, scope out, about 10 times on this floor).
  - Crept past the TV guard at 1.0-1.3 m in TV glow (light 50); he never reacted. OPSAT tip confirmed: he only
    reacts to noise.
  - Up the stairs jamming the hallway lamps.
- **Zherkezhi's Room - the end.**
  - He used a computer under the monitor wall (SHUTDOWN COMPUTER / SWITCH OBJECT, then a file screen,
    11:01-11:06). The screens lit Sam to 115.
  - OPSAT's radar read **FACING YOU 2** the whole time. The two guards went alert as he walked off (11:09-11:10);
    he quit.
  - **Check the radar's FACING YOU count before using anything that lights up. Computers and monitor walls light
    Sam like a lamp.**

**What changed from the first day (his adaptation):**
- The OCP is now his main tool: scope (middle click), jam (right click), unscope, before every lit stretch.
- He waits for the charge instead of moving without it.
- Decoys (switch or jammed lamp) pull guards before the risky climb.
- No hurrying after a jam.
- Interrogate before knocking out.
- Skip takedowns where passing behind works (Guest Room).
- Every alert today traced to one of four things:
  - being lit in a guard's view (Helipad ladder, Zherkezhi's computer);
  - the left-click rifle (Helipad);
  - a body left at the edge of a dark spot;
  - the wrong order with a held guard (which cost an objective, not an alert).

**OPSAT INTEL findings:**
- The detail-chief objective (Objective_0008) comes from the kitchen patroller too, much earlier than the
  greenhouse guard in walkthrough.json.
- The Helipad decoy also works by jamming the corridor lamp by the switch box.
- The Guest Room and TV-guard tips are right as written.
- OPSAT's radar FACING YOU count was the warning that mattered in Zherkezhi's room.

**Relaunch menus (seen 2026-10-06 06:17 after a crash):** the game opens straight on GAME MODE SELECTION
(SOLO highlighted). Esc there opens QUIT GAME? (NO is default - Enter = NO). SOLO -> MAIN MENU, where nothing
is highlighted after the transition; the Enter meant for CONTINUE did nothing. Fix needed in launcher.py:
no blind Esc; on MAIN MENU press S then W to focus CONTINUE, then Enter; recognise screens from these shots
(Saved Games/OPSAT/runs/launch/20261006_06*).

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
| 6 | Penthouse (Pranav, human) | Kitchen, quit | 2026-10-08: alley high road -> elevator shaft -> helipad (attempt 2: switch decoy + floodlight OCP) -> neon sign zip-line -> balcony interrogation -> crawl space -> guest room -> kitchen; 4 grabs from behind, 2 objectives; ended by a left-click rifle burst meant to be a sticky shocker (right click) |
| 7 | Penthouse (Pranav, human) | Zherkezhi's Room, quit | 2026-10-10: 13 Helipad attempts, clean crossing with decoy + floodlight OCP; balcony interrogation (redone after knocking out first); clean construction floor and guest room; two kitchen takedowns, detail-chief objective from the kitchen guard; TV guard passed at 1 m; seen at Zherkezhi's lit computer |

Recording a human session (start it BEFORE playing): `tools/bot/record_video.py [minutes] [fps=6]` - game window
to MP4 in 10-minute segments, frames.csv timestamps matching the run log, inputs.csv (keys and mouse buttons,
only while the game is in front). Review: `tools/bot/playstudy.py RUN.jsonl` for the timeline, then
`tools/bot/framesheet.py VIDEO_DIR RUN.jsonl FROM TO STEP OUT.jpg [--hud]` to look at every moment that matters
(--hud enlarges ammo, OCP charge and noise meter). Explain each event from the frames before writing a lesson:
position data alone gave three wrong causes in the first write-up of run 6.
