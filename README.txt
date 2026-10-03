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
  Left / Right  switch tab: TERMINAL <-> RADAR
  Insert        ask DVORAK (Enter sends, Esc cancels)
  The arrow keys are taken off movement in your game profile; WASD moves Sam.
  F2 / F3 still toggle god mode / invisible, but OPSAT and DVORAK ignore them.

TERMINAL  Live next-moves card (where you are, primaries done, next objective + route, what you
          can do there, any instant-fail rule) above DVORAK. DVORAK = Claude Haiku 4.5; it asks
          for your Anthropic API key once, plans for a pure stealth operator, and remembers your
          campaign across sessions.
RADAR     Objectives with live ticks and the fail rule on top, the NEXT card and route, then the
          radar (guards and cones, cameras, objective markers) with alarm / alert / suspicious /
          facing-you / camera counts.

Your data (API key, DVORAK memory, relationship, chat log) is in %USERPROFILE%\Saved Games\OPSAT and survives
reinstalling the game or the mod. Delete that folder to start DVORAK from scratch.

Mods: see mods\MODS.txt. Uninstall: double-click "Uninstall OPSAT.cmd".
