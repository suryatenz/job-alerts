"""
Opens one approved job's application page in a real, visible browser and fills
in what's confidently known — then STOPS. It never clicks Submit, and it never
fills EEO/demographic self-identification fields (gender, ethnicity, veteran/
disability status) — those are always left for you.

Usage: python apply_to_job.py <job_id>

Works on Greenhouse and Lever postings only. Anything else just opens the page
unfilled with a note to fill it manually.
"""

import os
import sys

from playwright.sync_api import sync_playwright

from applications_store import load_applications
from draft_applications import load_profile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

EEO_KEYWORDS = (
    "gender", "race", "racial", "ethnic", "hispanic", "latino", "latina",
    "veteran", "disability", "disabled", "sexual orientation", "pronoun",
    "transgender", "lgbtq",
)

GREENHOUSE_EEO_IDS = ("gender", "hispanic_ethnicity", "veteran_status", "disability_status")


def is_eeo_question(text):
    t = (text or "").lower()
    return any(kw in t for kw in EEO_KEYWORDS)


def detect_ats(apply_url):
    if "greenhouse.io" in apply_url:
        return "greenhouse"
    if "lever.co" in apply_url:
        return "lever"
    return "unsupported"


def try_fill_selector(page, selector, value, label, filled, unfilled, skip_ids=()):
    if not value:
        unfilled.append(f"{label} (no value stored)")
        return
    try:
        el_id = selector.lstrip("#")
        if el_id in skip_ids:
            return  # never touch EEO fields, no matter what
        loc = page.locator(selector)
        if loc.count() == 0:
            unfilled.append(f"{label} (field not found)")
            return
        loc.first.fill(str(value))
        filled.append(label)
    except Exception as e:
        unfilled.append(f"{label} (couldn't fill: {e.__class__.__name__})")


def try_fill_by_label(page, question, answer, filled, unfilled, skip_ids=()):
    if is_eeo_question(question):
        return  # never answer EEO/demographic questions — always left for the user
    if not answer:
        return
    try:
        loc = page.get_by_label(question, exact=False)
        if loc.count() == 0:
            unfilled.append(f"{question} (no matching field found)")
            return
        target = loc.first
        el_id = target.get_attribute("id") or ""
        if el_id in skip_ids:
            return
        target.fill(str(answer))
        filled.append(question)
    except Exception as e:
        unfilled.append(f"{question} (couldn't fill: {e.__class__.__name__})")


def fill_greenhouse(page, profile, answers, resume_path):
    filled, unfilled = [], []

    try_fill_selector(page, "#first_name", profile.get("first_name", ""), "First name", filled, unfilled)
    try_fill_selector(page, "#last_name", profile.get("last_name", ""), "Last name", filled, unfilled)
    try_fill_selector(page, "#email", profile.get("email", ""), "Email", filled, unfilled)
    try_fill_selector(page, "#phone", profile.get("phone", ""), "Phone", filled, unfilled)

    if resume_path and os.path.exists(resume_path):
        try:
            page.locator("#resume").set_input_files(resume_path)
            filled.append("Resume upload")
        except Exception as e:
            unfilled.append(f"Resume upload (couldn't attach: {e.__class__.__name__})")
    else:
        unfilled.append("Resume upload (resume.pdf not found)")

    for question, answer in answers.items():
        try_fill_by_label(page, question, answer, filled, unfilled, skip_ids=GREENHOUSE_EEO_IDS)

    return filled, unfilled


def fill_lever(page, profile, answers, resume_path):
    filled, unfilled = [], []

    try_fill_selector(page, 'input[name="name"]', profile.get("full_name", ""), "Full name", filled, unfilled)
    try_fill_selector(page, 'input[name="email"]', profile.get("email", ""), "Email", filled, unfilled)
    try_fill_selector(page, 'input[name="phone"]', profile.get("phone", ""), "Phone", filled, unfilled)
    try_fill_selector(page, 'input[name="urls[LinkedIn]"]', profile.get("linkedin", ""), "LinkedIn", filled, unfilled)
    try_fill_selector(page, 'input[name="urls[GitHub]"]', profile.get("github", ""), "GitHub", filled, unfilled)

    if resume_path and os.path.exists(resume_path):
        try:
            resume_input = page.locator('input[name="resume"]')
            if resume_input.count() == 0:
                resume_input = page.locator('input[type="file"]')
            resume_input.first.set_input_files(resume_path)
            filled.append("Resume upload")
        except Exception as e:
            unfilled.append(f"Resume upload (couldn't attach: {e.__class__.__name__})")
    else:
        unfilled.append("Resume upload (resume.pdf not found)")

    for question, answer in answers.items():
        try_fill_by_label(page, question, answer, filled, unfilled)

    return filled, unfilled


def main():
    if len(sys.argv) < 2:
        print("Usage: python apply_to_job.py <job_id>")
        sys.exit(1)

    job_id = sys.argv[1]
    apps = load_applications()
    entry = apps.get(job_id)
    if not entry:
        print(f"No application found for job_id: {job_id}")
        sys.exit(1)
    if entry["status"] != "approved":
        print(f"Job status is '{entry['status']}', not 'approved' — approve it on the review page first.")
        sys.exit(1)

    profile = load_profile()
    resume_path = os.path.join(PROJECT_ROOT, "resume", "resume.pdf")

    answers = {}
    answers.update(profile.get("answers", {}))
    answers.update(entry.get("standard_answers", {}))

    apply_url = entry["apply_url"]
    ats = detect_ats(apply_url)

    # Lever's job description page has no form at all — the real application
    # form lives at a separate /apply sub-path.
    nav_url = apply_url
    if ats == "lever" and not apply_url.rstrip("/").endswith("/apply"):
        nav_url = apply_url.rstrip("/") + "/apply"

    print(f"Job: {entry['title']} @ {entry['company']}")
    print(f"ATS detected: {ats}")
    print(f"Opening {nav_url} ...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)  # let client-rendered forms (Lever) finish mounting

        if ats == "greenhouse":
            filled, unfilled = fill_greenhouse(page, profile, answers, resume_path)
        elif ats == "lever":
            filled, unfilled = fill_lever(page, profile, answers, resume_path)
        else:
            filled, unfilled = [], []
            print("This posting isn't on Greenhouse or Lever — opening it unfilled for you to complete manually.")

        print(f"\nFilled ({len(filled)}): {', '.join(filled) if filled else '(none)'}")
        print(f"Left blank ({len(unfilled)}): {', '.join(unfilled) if unfilled else '(none)'}")
        print("\nReview the form yourself before submitting — this never clicks Submit, "
              "and demographic/EEO questions are always left for you to answer.")
        print("Close the browser window when you're done to exit this script.")

        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
