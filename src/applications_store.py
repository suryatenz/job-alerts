"""
Shared load/save for applications.json — the draft application store.
Used by both main.py (writes new drafts) and review_server.py (reads/updates them).
"""

import json
import os
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
APPLICATIONS_FILE = os.path.join(PROJECT_ROOT, "data", "applications.json")

os.makedirs(os.path.dirname(APPLICATIONS_FILE), exist_ok=True)


def load_applications():
    if not os.path.exists(APPLICATIONS_FILE):
        return {}
    with open(APPLICATIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_applications(apps):
    with open(APPLICATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(apps, f, indent=4)


def merge_new_drafts(apps, drafts):
    """Adds newly-drafted jobs as 'pending'. Skips any job_id already present
    (matches the seen_jobs.json dedupe spirit — never overwrite a draft the
    user might already be reviewing or has already acted on)."""
    today = datetime.now().strftime("%Y-%m-%d")
    added = 0
    for d in drafts:
        job_id = d["job_id"]
        if job_id in apps:
            continue
        apps[job_id] = {
            "title": d["title"],
            "company": d["company"],
            "apply_url": d["apply_url"],
            "why_this_role": d["why_this_role"],
            "standard_answers": d["standard_answers"],
            "flagged_questions": d["flagged_questions"],
            "status": "pending",
            "date_added": today,
        }
        added += 1
    return apps, added
