"""Stop every running bot (fisher.py / train_ppo.py) and release any keys it was holding.

  python rl/stop_bot.py

The interpreter is python3.13.exe (Windows Store alias), so killing 'python.exe' by image name misses it;
processes are matched by command line instead.
"""
import subprocess

PS = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'fisher\\.py|train_ppo\\.py' "
      "-and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }")


def stop():
    out = subprocess.run(['powershell', '-NoProfile', '-Command', PS], capture_output=True, text=True).stdout.split()
    try:
        from scct.controls import SC, key
        for name in SC:
            key(name, True)                          # key up for everything the bot can press
    except Exception:
        pass
    return out


if __name__ == '__main__':
    pids = stop()
    print('stopped', len(pids), 'process(es)', ' '.join(pids))
