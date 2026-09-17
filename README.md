# Job Alerts

Daily digest of ~30 job postings matched against your resume, with direct apply links,
plus draft application content ready for review. Runs locally, no hosting required.

## Project structure

```
job-alerts/
  config.json, config.example.json, companies.json    # project config
  requirements.txt, README.md, .gitignore

  src/               # all code — run these directly, e.g. `python src/main.py`
    main.py, setup_scheduler.py, review_server.py, apply_to_job.py   # entry points
    tailor_resume.py, skills_gap.py                                  # on-demand, free tools
    fetch_jobs.py, rank_jobs.py, draft_applications.py,
    applications_store.py, notify_email.py, notify_whatsapp.py       # core pipeline modules

  profile/           # your application "memory" — personal, gitignored except the template
    profile.json
    profile.example.json

  resume/            # your resume assets — personal, entirely gitignored
    resume.pdf
    resume_template.tex   # LaTeX source, master version
    master_reference.md   # fuller source of truth (real projects/experience)
    generated/<date>/     # tailored resumes — PDFs only, this is what you browse
    _dump/<date>/         # tailoring build scrap (.tex/.aux/.log) — not the deliverable
    examples/              # one-off tailored variants (not wired into the pipeline)
      automation_engineer.tex
      automation_engineer.pdf

  data/              # runtime state — regenerated on every run, entirely gitignored
    applications.json
    seen_jobs.json

  logs/              # entirely gitignored
    job_log.txt
```

Every script resolves `config.json`/`profile/`/`resume/`/`data/`/`logs/` via an absolute `PROJECT_ROOT` (one level above `src/`), not the current working directory — so it doesn't matter where you run a command *from*, as long as the file itself stays in `src/` alongside its siblings.

## Cost

| Piece | Cost |
|---|---|
| Job data (SimplifyJobs, Greenhouse, Lever, RemoteOK, Adzuna) | $0 |
| Ranking against your resume (Claude Haiku) | ~$1.30/month (separate from your Claude Pro subscription — pay-per-token API billing at [console.anthropic.com](https://console.anthropic.com)) |
| Drafting application content (`draft_model`, default Claude Sonnet 5) | ~$3-5/month at 30 jobs/day (real measured cost: ~$0.10-0.11/run) |
| Daily scheduling (Windows Task Scheduler) | $0 |
| Email delivery (Gmail SMTP) | $0 |
| Review webpage (local Flask app) | $0 |
| Application form-filling (Playwright/Chromium) | $0 (one-time ~200-300MB browser download) |
| WhatsApp ping (Twilio) | ~$1-3/month, only if enabled |
| **Total** | **~$4-6/month** (ranking + drafting), plus WhatsApp if you turn it on. Set `draft_model` back to `claude-haiku-4-5` in `config.json` for the cheaper ~$2-3/month total, at lower writing quality. |

If the Anthropic API key runs out of credit or isn't configured, ranking falls back to a simple keyword-match score automatically — the digest still sends, just less precisely ranked.

## Setup

1. `pip install -r requirements.txt`
2. Copy `config.example.json` to `config.json` and fill in:
   - `sender_email` / `sender_app_password` — Gmail address + [app password](https://myaccount.google.com/apppasswords) (not your regular password)
   - `anthropic_api_key` — from console.anthropic.com. Needs a small credit balance loaded (this is separate from the $20/mo Claude Pro subscription).
   - `titles` / `locations` / `include_internships` / `max_years_experience` — adjust to taste
3. Put your resume at `resume/resume.pdf` (and `resume/resume_template.tex` too, if you're using the LaTeX-based tailoring workflow).
4. Copy `profile/profile.example.json` to `profile/profile.json` and fill in your real details — `first_name`/`last_name` (kept separate from `full_name` so form-filling doesn't have to guess how to split it), contact info, visa/work-authorization status, and (once you know them) notice period, salary expectation, relocation preference. This file is your growing "application memory."
5. For the "Open & Fill Application" feature: `playwright install chromium` once (one-time ~200-300MB download, free, downloads a real Chromium browser Playwright drives).
6. (Optional, free) Adzuna — broadens job sources beyond the curated `companies.json` list. Sign up free at [adzuna.com/developer](https://developer.adzuna.com/), no card required, fill in `adzuna_app_id` / `adzuna_app_key`.
7. (Optional, ~$1-3/mo) WhatsApp via Twilio:
   - Create a free Twilio account, enable a WhatsApp Sender
   - Submit one **utility**-category message template for approval, e.g.: `Your job digest is ready — {{1}} new matches today. Check your email for details.`
   - Once approved, fill in `twilio_account_sid`, `twilio_auth_token`, `twilio_whatsapp_from`, `twilio_whatsapp_to`, `twilio_content_sid`, and set `twilio_enabled: true`
   - Test it standalone before relying on it: `cd src && python -c "from notify_whatsapp import send_whatsapp_alert; import json; send_whatsapp_alert([], json.load(open('../config.json')))"`

## Daily workflow

1. `src/main.py` runs automatically (or manually) — fetches, ranks, and **drafts application content** for new matches, then emails you a summary.
2. Run `python src/review_server.py`, which opens `http://127.0.0.1:5000` in your browser (localhost only — never exposed to the network).
3. For each pending draft: edit the "why this role" note and standard answers if needed, answer any flagged questions, then **Approve** or **Reject**.
4. Approved jobs on Greenhouse or Lever get an **"Open & Fill Application"** button — click it and a real, visible browser opens the actual application page with your name/email/phone/resume/LinkedIn and matched custom answers already filled in.
5. **Nothing gets submitted automatically, ever.** The filled browser window waits for you — review it, fix anything it missed (it always skips demographic/EEO questions on purpose), and click Submit yourself.
6. After you've actually applied, click **"Mark as Submitted"** on the review page — this is a manual confirmation you make, never something the system infers.
7. Anything you edit or answer along the way is remembered in `profile/profile.json` and reused automatically in future drafts — the number of flagged questions per day should shrink over time as your profile fills in.

Postings from sources other than Greenhouse/Lever (most of SimplifyJobs' feed routes through Ashby, Workday, or company-custom sites) don't get the fill step — approve them the same way, then apply manually via the link.

- Manually run the pipeline: `python src/main.py`
- Daily automatically: run `python src/setup_scheduler.py` once (no admin rights needed — it registers a standard, non-elevated Task Scheduler job called `JobAlertsDigest` that runs `src/main.py` daily at the time set in `config.json`'s `send_time`, default 18:55)
- Logs go to `logs/job_log.txt`. Remove the task with `schtasks /delete /tn JobAlertsDigest /f`.

## Resume tailoring (on-demand, per-job — never automatic)

`resume/resume_template.tex` is the LaTeX source behind `resume/resume.pdf` (a RenderCV-style template). Compiling it locally uses MiKTeX (`pdflatex`), installed once in user-mode — no admin rights needed.

This is deliberately **not** wired into the daily `main.py` run — at 30 jobs/day it would cost ~$56/month just for resumes. Instead, `src/tailor_resume.py` is on-demand:

```
python tailor_resume.py <job_id>     one specific job, by its exact id
python tailor_resume.py tiktok       case-insensitive title/company search (lists matches if ambiguous)
```

It rewords/reorders/re-emphasizes real resume content for that job (never fabricates), and scores both your real resume and the tailored one against that job's description (0-100, via Haiku — cheap) so you can see whether it actually helped. Real cost: ~$0.06/resume.

Output is split so `resume/generated/<YYYY-MM-DD>/` only ever holds the finished PDF you actually want to open/download — the LaTeX build scrap (`.tex`/`.aux`/`.log`) goes into `resume/_dump/<YYYY-MM-DD>/` instead, out of the way but kept in case you ever need to inspect the raw source.

The same thing is reachable from the review page (`review_server.py`) — approved jobs get a **"Generate Tailored Resume"** button. Clicking it swaps the button for a "generating..." status and polls automatically; once it finishes (usually 20-30s), the download link and both scores appear right there next to the apply link, no page refresh needed.

Both this and application drafting share a strict writing-style ruleset baked into the prompt (no hyphens or dashes anywhere, a banned-vocabulary list of AI-sounding words/phrases, no cliche openers, no formulaic AI sentence patterns) so the output reads like something you'd actually write, not something a model generated. It's not perfect — an LLM won't hit 100% compliance on any style rule — but it catches the large majority.

`resume/master_reference.md` is the fuller source of truth this pulls from (or will, once wired in) — your complete real project/experience history, richer than what fits on the one-page resume, with anything unverified explicitly flagged rather than guessed.

## Skills gap analysis (free, local, no API calls)

`src/skills_gap.py` scans every job description accumulated in `data/applications.json` so far, counts how often common tools/skills show up, and checks which of those aren't anywhere in your resume or master reference. Run it any time: `python skills_gap.py`. Pure keyword frequency counting — no Claude call, $0 — meant to answer "what do these postings keep asking for that I don't have," not to replace judgment about what's actually worth learning.

## Adding more companies

`companies.json` lists Greenhouse/Lever company board slugs to pull from directly. To add one: check if the company's careers page is hosted at `job-boards.greenhouse.io/<slug>` or `jobs.lever.co/<slug>`, then add `<slug>` to the matching list. Bad slugs are logged as warnings and skipped — they won't break the run.

## How matching works

1. `fetch_jobs.py` pulls every open posting from all sources — SimplifyJobs (community-maintained new-grad/internship listings, tagged with sponsorship + degree requirements), Greenhouse, Lever, RemoteOK, Adzuna (typically 10,000+ raw postings)
2. `rank_jobs.py` pre-filters by title keywords, drops senior/staff-tier titles, collapses same-role/same-company duplicates, fetches full descriptions for the shortlist, then applies three hard filters before anything reaches Claude:
   - **Experience**: drops postings whose description states a floor above `max_years_experience`
   - **Sponsorship**: drops postings tagged/stated as no-sponsorship or citizens-only
   - **Degree**: drops PhD-only postings
3. The survivors go to Claude Haiku in **one batched call** to score fit against your resume; the top `max_jobs_per_day` are selected
4. `draft_applications.py` sends those selected jobs (with full descriptions) + your `profile/profile.json` to Claude in a **second batched call**, drafting a tailored "why this role" note and filling in standard answers, flagging anything the profile doesn't cover
5. Drafts land in `data/applications.json`; `review_server.py` is where you review, edit, approve, or reject them — and where corrections feed back into `profile/profile.json` for next time
