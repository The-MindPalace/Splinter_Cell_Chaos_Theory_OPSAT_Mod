OPSAT V1.2 - Splinter Cell: Chaos Theory overlay mod
=====================================================

Install (on a fresh Steam install):
  1. Install the game from Steam and run it once (create your profile).
  2. Double-click "Install OPSAT.cmd". It finds the game, installs the Python packages it needs,
     copies the overlay, sets the keys, puts the game on your dedicated GPU, installs the
     Widescreen Fix (borderless - OPSAT needs it to be visible) and DXVK from mods\, and makes
     OPSAT start with Windows.
  3. Start the game. In Options > Graphics, check 1920x1080 and High settings once.

Controls:
  Up arrow      slide OPSAT up (bottom-left corner); it always opens on RADAR
  Down arrow    slide it away
  Left / Right  one panel over: DVORAK <- RADAR -> INTEL (stops at the ends; the panel glides to each height)
  Insert        open DVORAK and type (Enter sends, Esc cancels; at the API key prompt Ctrl+V pastes)
  The arrow keys are taken off movement in your game profile; WASD moves Sam.
  F2 / F3 still toggle god mode / invisible, but OPSAT and DVORAK ignore them.

RADAR     Opens first. NEXT names the objective by the same short name its radar diamond carries
          ("Zherkezhi's server"), the full game title under it, distance and clock bearing on the right.
          HERE (green) is the goal of the area you are standing in. Then the radar: 20 m rim, guards with
          view cones (green calm, amber suspicious, red alert, hollow = another floor), cameras (filled
          squares), sensors (outlined), objective diamonds with a small arrow and "▲3m" / "▼4m" when
          they are above or below you. Objectives past the rim point from the edge with name and distance;
          ones in the same direction share a pointer ("Server +2 65m"). Objectives the game has not given
          you yet never show. On the right: alarms, then alert / suspicious / facing you / cameras near you.
INTEL     NEXT (where the objective is, the rooms to go through), then FROM HERE, the big green block:
          the area you are in, what to do there and how, step by step. Below it the objective's own ways
          through, and the checklist with the mission's fail condition.
DVORAK    The chat. DVORAK = Claude Haiku 4.5; it asks for your Anthropic API key (again if a key is
          rejected), plans for a pure stealth operator, and remembers your campaign across sessions.
With the panel closed, a small strip bottom-left shows the worst live threat and GOD / INVISIBLE.
During an alarm the whole panel's accent turns red and a banner says how many guards are hunting.

Ways through (free): every objective in all 10 missions has its location and 2-4 stealth ways
through it, researched once from several full walkthroughs and cross-checked (door codes included).
OPSAT reads it locally from CheatOverlay\walkthrough.json; the readable version is WALKTHROUGH.md.
Nothing is looked up online while you play. DVORAK only calls the API when you press Insert and ask
(no web search, no automatic SITREPs), and answers from the same field notes plus live telemetry.

Run recorder: every mission you play leaves a compact track (room, position, alarm, nearby guards, twice a
second; objective changes) in %USERPROFILE%\Saved Games\OPSAT\runs - local only. It is the training set for
the Sam bot (tools\bot, playbook in training\LESSONS.md).

Your data (API key, DVORAK memory, relationship, chat log) is in %USERPROFILE%\Saved Games\OPSAT and survives
reinstalling the game or the mod. Delete that folder to start DVORAK from scratch.

Mods: see mods\MODS.txt. Uninstall: double-click "Uninstall OPSAT.cmd".
