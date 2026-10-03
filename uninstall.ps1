# OPSAT V1.2 uninstaller - removes the overlay and its auto-start. Keeps Saved Games\OPSAT
# (your API key, DVORAK's memory and bond) unless you delete that folder yourself.
$ErrorActionPreference = 'Continue'
Get-CimInstance Win32_Process -Filter "Name like 'pythonw%'" |
    Where-Object { $_.CommandLine -like '*cheat_overlay.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-ChildItem ([Environment]::GetFolderPath('Startup')) -Filter 'Splinter Cell*.lnk' | Remove-Item -Force
$steam = ((Get-ItemProperty 'HKCU:\Software\Valve\Steam' -ErrorAction SilentlyContinue).SteamPath) -replace '/', '\'
foreach ($root in @($steam, 'C:\Program Files (x86)\Steam')) {
    if (-not $root) { continue }
    $dir = Join-Path $root 'steamapps\common\Splintercell Chaos Theory\CheatOverlay'
    if (Test-Path $dir) { Remove-Item $dir -Recurse -Force; Write-Host "Removed $dir" }
}
Write-Host "OPSAT removed. Your data is still in $env:USERPROFILE\Saved Games\OPSAT."
