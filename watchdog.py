"""Auto-restart watchdog for the mayhem sniper bot.

Runs bot_runner.py as a child process and relaunches it whenever it exits
(crash, RPC death, etc.), waiting 5s between restarts. Launch this with nohup
so it survives the shell; the bot's own .bot.pid lock still prevents doubles.
"""

import os
import subprocess
import sys
import time

os.environ["PYTHONPATH"] = "src" + os.pathsep + os.environ.get("PYTHONPATH", "")

while True:
    print("[watchdog] starting bot_runner ...", flush=True)
    proc = subprocess.Popen([sys.executable, "-u", "src/bot_runner.py"])
    code = proc.wait()
    print(f"[watchdog] bot exited (code {code}); restarting in 5s ...", flush=True)
    time.sleep(5)
