"""
Run this ONCE to register a Windows Task Scheduler job that runs main.py
every day at the time set in config.json ("send_time"). Runs as the current
user at standard (not elevated) privileges — main.py doesn't need admin
rights, so no UAC prompt is required.
"""

import subprocess
import os
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
MAIN_SCRIPT = os.path.join(SCRIPT_DIR, "main.py")
CONFIG = os.path.join(PROJECT_ROOT, "config.json")
TASK_NAME = "JobAlertsDigest"


def main():
    with open(CONFIG, encoding="utf-8") as f:
        config = json.load(f)

    send_time = config.get("send_time", "18:55")
    python_exe = r"C:\Users\spraj\anaconda3\python.exe"

    print(f"Python   : {python_exe}")
    print(f"Script   : {MAIN_SCRIPT}")
    print(f"Schedule : daily at {send_time}")
    print()

    subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True
    )

    cmd = [
        "schtasks", "/create",
        "/tn", TASK_NAME,
        "/tr", f'"{python_exe}" "{MAIN_SCRIPT}"',
        "/sc", "DAILY",
        "/st", send_time,
        "/f",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"SUCCESS  : Task '{TASK_NAME}' scheduled daily at {send_time}.")
        print()
        print("To change the send time: edit 'send_time' in config.json then re-run this script.")
        print(f"To remove the task:      schtasks /delete /tn {TASK_NAME} /f")
    else:
        print(f"FAILED: {result.stderr.strip()}")


if __name__ == "__main__":
    main()
