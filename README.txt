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
  Up arrow      slide OPSAT up (bottom-left)
  Down arrow    slide it away
  Left / Right  switch panel: DVORAK <-> INTEL <-> RADAR (the panel glides to each one's height)
  Insert        ask DVORAK (Enter sends, Esc cancels)
  The arrow keys are taken off movement in your game profile; WASD moves Sam.
  F2 / F3 still toggle god mode / invisible, but OPSAT and DVORAK ignore them.

DVORAK    The chat. DVORAK = Claude Haiku 4.5; it asks for your Anthropic API key (again if a key is
          rejected), plans for a pure stealth operator, and remembers your campaign across sessions.
INTEL     Opens first. The next objective and its distance, FROM HERE (what to do in the exact area you
          are standing in), the objective's own ways through, then the objectives checklist.
RADAR     The radar first: guards and view cones, cameras, and objective markers labelled with short
          names ("Vault panels", "Punch cards"), plus distance for ones off the edge. A slim NEXT bar on
          top, alarm / alert / suspicious / facing-you / camera counts below.

Ways through (free): every objective in all 10 missions has its location and 2-4 stealth ways
through it, researched once from several full walkthroughs and cross-checked (door codes included).
OPSAT reads it locally from CheatOverlay\walkthrough.json; the readable version is WALKTHROUGH.md.
Nothing is looked up online while you play. DVORAK only calls the API when you press Insert and ask
(no web search, no automatic SITREPs), and answers from the same field notes plus live telemetry.

Your data (API key, DVORAK memory, relationship, chat log) is in %USERPROFILE%\Saved Games\OPSAT and survives
reinstalling the game or the mod. Delete that folder to start DVORAK from scratch.

Mods: see mods\MODS.txt. Uninstall: double-click "Uninstall OPSAT.cmd".
