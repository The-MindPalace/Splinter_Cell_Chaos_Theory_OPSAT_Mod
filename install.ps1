# OPSAT V1.2 installer - adds the OPSAT overlay (TERMINAL + RADAR, DVORAK) to a clean
# Splinter Cell: Chaos Theory (Steam) install. Safe to run again at any time (it just refreshes everything).
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
function Say($msg, $colour = 'Green') { Write-Host $msg -ForegroundColor $colour }

Say "`n  OPSAT V1.2 // installer`n"

# 1. Find the game -----------------------------------------------------------------------------
function Find-Game {
    $libs = @()
    $steam = (Get-ItemProperty 'HKCU:\Software\Valve\Steam' -ErrorAction SilentlyContinue).SteamPath
    if ($steam) {
        $steam = $steam -replace '/', '\'
        $libs += $steam
        $vdf = Join-Path $steam 'steamapps\libraryfolders.vdf'
        if (Test-Path $vdf) {
            Select-String -Path $vdf -Pattern '"path"\s+"(.+?)"' | ForEach-Object {
                $libs += ($_.Matches[0].Groups[1].Value -replace '\\\\', '\')
            }
        }
    }
    $libs += 'C:\Program Files (x86)\Steam'
    foreach ($lib in $libs) {
        $dir = Join-Path $lib 'steamapps\common\Splintercell Chaos Theory'
        if (Test-Path (Join-Path $dir 'System\splintercell3.exe')) { return $dir }
    }
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = 'Select your Splinter Cell Chaos Theory folder (the one that contains "System")'
    if ($dlg.ShowDialog() -eq 'OK' -and (Test-Path (Join-Path $dlg.SelectedPath 'System\splintercell3.exe'))) {
        return $dlg.SelectedPath
    }
    throw 'Splinter Cell Chaos Theory folder not found.'
}
$game = Find-Game
Say "  Game:    $game"

# 2. Python + the Anthropic SDK ----------------------------------------------------------------
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
$py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw -or -not $py) {
    Say '  Python 3 was not found.' 'Yellow'
    $ans = Read-Host '  Install Python 3.13 now with winget? (Y/N)'
    if ($ans -notmatch '^[Yy]') { throw 'Python is required. Install it from python.org, then run this again.' }
    winget install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (-not $pyw) { throw 'Python installed, but pythonw.exe is not on PATH yet. Sign out and in, then run this again.' }
}
Say "  Python:  $py"
& $py -m pip install --user --upgrade --quiet anthropic pillow
if ($LASTEXITCODE -ne 0) { throw 'pip could not install the anthropic package.' }
Say '  Python packages installed (anthropic, pillow).'

# 3. Stop a running copy, then copy the overlay into the game folder ---------------------------
Get-CimInstance Win32_Process -Filter "Name like 'pythonw%'" |
    Where-Object { $_.CommandLine -like '*cheat_overlay.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
$dest = Join-Path $game 'CheatOverlay'
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item (Join-Path $here 'CheatOverlay\*') $dest -Recurse -Force
Say "  Overlay: $dest"

# 4. Start with Windows (waits quietly for the game, however it is launched) -------------------
$startup = [Environment]::GetFolderPath('Startup')
Get-ChildItem $startup -Filter 'Splinter Cell*.lnk' -ErrorAction SilentlyContinue | Remove-Item -Force
$lnk = Join-Path $startup 'Splinter Cell OPSAT V1.2.lnk'
$sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$sc.TargetPath = $pyw
$sc.Arguments = '"' + (Join-Path $dest 'cheat_overlay.py') + '"'
$sc.WorkingDirectory = $dest
$sc.Description = 'Splinter Cell OPSAT V1.2 overlay'
$sc.Save()
Say '  Auto-start shortcut created.'

# 5. Cheat key binds in every game profile (F2 god, F3 invisible, F4 ghost, F6 ammo, F7 health) --
$binds = [ordered]@{ 'F2' = 'Invincible 1'; 'F3' = 'Invisible 1'; 'F4' = 'ghost'; 'F6' = 'ammo'; 'F7' = 'health'
           # Arrow keys belong to OPSAT (up open, down close, left/right tabs); WASD still moves Sam.
           'Left' = ''; 'Up' = ''; 'Right' = ''; 'Down' = '' }
$profiles = Join-Path $env:ProgramData "Ubisoft\Tom Clancy's Splinter Cell Chaos Theory\Profiles"
$inis = @(Get-ChildItem $profiles -Recurse -Filter *.ini -ErrorAction SilentlyContinue |
          Where-Object { (Get-Content $_.FullName -Raw) -match '\[Engine\.Input\]' })
foreach ($ini in $inis) {
    $text = Get-Content $ini.FullName -Raw
    foreach ($k in $binds.Keys) {
        $text = [regex]::Replace($text, "(?m)^$k=[^\r\n]*", "$k=$($binds[$k])")
    }
    [IO.File]::WriteAllText($ini.FullName, $text, [Text.Encoding]::Default)
    Say "  Cheat keys bound in profile: $($ini.Directory.Name)"
}
if (-not $inis) {
    Say '  No game profile yet: start the game once, create your profile, then run this installer again' 'Yellow'
    Say '  (only needed for the F2/F3 cheat keys - OPSAT itself works without it).' 'Yellow'
}

# 6. Run the game on the dedicated GPU (laptops otherwise start it on the integrated chip, where the
#    game auto-detects weak hardware and drops to 1024x768 low settings - the "blurry" look) ------
$exe = Join-Path $game 'System\splintercell3.exe'
New-Item -Path 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences' -Force | Out-Null
New-ItemProperty -Path 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences' -Name $exe -Value 'GpuPreference=2;' -PropertyType String -Force | Out-Null
Say '  Windows graphics preference: High performance GPU.'

# 7. Borderless fullscreen via ThirteenAG's Widescreen Fix. OPSAT cannot draw over exclusive fullscreen,
#    so the game has to run borderless. Downloaded from the official GitHub release if missing. --------
$ws = Join-Path $game 'System\scripts\SplinterCellChaosTheory.WidescreenFix.ini'
$bundled = Join-Path $here 'mods\SplinterCellChaosTheory.WidescreenFix.zip'
if (-not (Test-Path $ws) -and (Test-Path $bundled)) {
    Expand-Archive $bundled -DestinationPath $game -Force
    Say '  Widescreen Fix installed from mods\ (bundled copy).'
}
if (-not (Test-Path $ws)) {
    $url = 'https://github.com/ThirteenAG/WidescreenFixesPack/releases/download/scct/SplinterCellChaosTheory.WidescreenFix.zip'
    Say '  Widescreen Fix not found. OPSAT needs it (borderless fullscreen, proper 16:9).' 'Yellow'
    $ans = Read-Host '  Download SplinterCellChaosTheory.WidescreenFix.zip (~14 MB, ThirteenAG on GitHub) and install it? (Y/N)'
    if ($ans -match '^[Yy]') {
        $zip = Join-Path $env:TEMP 'SplinterCellChaosTheory.WidescreenFix.zip'
        Invoke-WebRequest $url -OutFile $zip -UseBasicParsing
        Expand-Archive $zip -DestinationPath $game -Force
        Remove-Item $zip -Force
        Say '  Widescreen Fix installed.'
    }
}
if (Test-Path $ws) {
    $t = [IO.File]::ReadAllText($ws)
    foreach ($k in 'ForceWindowedMode', 'ForceWindowStyle', 'DoNotNotifyOnTaskSwitch', 'UsePrimaryMonitor') {
        $t = [regex]::Replace($t, "(?m)^($k\s*=\s*)\d+", '${1}1')
    }
    [IO.File]::WriteAllText($ws, $t)
    Say '  Widescreen Fix: borderless fullscreen on, focus-loss handling off.'
} else {
    Say '  Without the Widescreen Fix the game runs exclusive fullscreen and OPSAT will not be visible.' 'Yellow'
}

# 7b. DXVK (Vulkan renderer): avoids the NVIDIA DirectX 9 driver crashes (nvd3dum.dll) seen when saving.
$dxvk = Join-Path $here 'mods\dxvk-2.7.1-x32-d3d9.dll'
if (-not (Test-Path $dxvk)) {
    $ans = Read-Host '  Download DXVK 2.7.1 (~10 MB, doitsujin on GitHub) to fix NVIDIA save crashes? (Y/N)'
    if ($ans -match '^[Yy]') {
        $tgz = Join-Path $env:TEMP 'dxvk-2.7.1.tar.gz'
        Invoke-WebRequest 'https://github.com/doitsujin/dxvk/releases/download/v2.7.1/dxvk-2.7.1.tar.gz' -OutFile $tgz -UseBasicParsing
        tar -xzf $tgz -C $env:TEMP 'dxvk-2.7.1/x32/d3d9.dll'
        Copy-Item (Join-Path $env:TEMP 'dxvk-2.7.12\d3d9.dll') $dxvk -Force
    }
}
if (Test-Path $dxvk) {
    Copy-Item $dxvk (Join-Path $game 'System\d3d9.dll') -Force
    Say '  DXVK installed (System\d3d9.dll).'
}

# 8. Go --------------------------------------------------------------------------------------
Start-Process $lnk
Say "`n  OPSAT V1.2 installed and running. Start the game and press the Up arrow."
Say "  DVORAK will ask for your Anthropic API key the first time you open the TERMINAL."
Say "  Your key, DVORAK's memory and your bond live in $env:USERPROFILE\Saved Games\OPSAT (kept across reinstalls).`n"
