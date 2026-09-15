"""
Daily Job Alert Digest
Fetches postings, ranks them against your resume, emails you the top matches,
and (if configured) pings WhatsApp. Run manually or via Task Scheduler.
"""

import json
import os
import traceback
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.json")
LOG_FILE = os.path.join(PROJECT_ROOT, "logs", "job_log.txt")

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(
            f"config.json not found at {CONFIG_FILE}. Copy config.example.json to config.json and fill it in."
        )
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def run():
    from fetch_jobs import fetch_all_jobs
    from rank_jobs import get_top_jobs
    from draft_applications import load_profile, generate_drafts
    from applications_store import load_applications, save_applications, merge_new_drafts
    from notify_email import send_digest
    from notify_whatsapp import send_whatsapp_alert

    config = load_config()

    log("Fetching jobs...")
    all_jobs = fetch_all_jobs(config)

    log("Ranking against resume...")
    top_jobs, ranking_cost = get_top_jobs(all_jobs, config)
    log(f"Top matches today: {len(top_jobs)} (Claude cost this run: ~${ranking_cost:.4f})")
    for j in top_jobs:
        log(f"  [{j.get('score')}] {j['title']} - {j['company']} ({j['location']})")

    log("Drafting applications...")
    profile = load_profile()
    drafts, draft_cost = generate_drafts(top_jobs, profile, config)
    apps = load_applications()
    apps, added = merge_new_drafts(apps, drafts)
    save_applications(apps)
    log(f"Drafted {len(drafts)} applications, {added} new (Claude cost this run: ~${draft_cost:.4f})")

    log("Sending email digest...")
    send_digest(top_jobs, config, applications_ready=added)
    log("Email sent.")

    log("Sending WhatsApp alert (if enabled)...")
    send_whatsapp_alert(top_jobs, config)

    log("Done.")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        log("FATAL ERROR:\n" + traceback.format_exc())
        raise
