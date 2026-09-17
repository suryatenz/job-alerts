"""
Local review webpage for draft applications.
Localhost-only (127.0.0.1) — never exposed to the network. Run manually:
    python review_server.py
then open http://127.0.0.1:5000

This page never submits anything to any real employer. It only lets you
review/edit drafts, approve or reject them, and answer flagged questions —
which is how profile.json's memory grows over time.
"""

import os
import subprocess
import sys
import threading
import webbrowser
from html import escape

from flask import Flask, request, redirect, url_for, send_file, jsonify

from applications_store import load_applications, save_applications
from apply_to_job import detect_ats
from draft_applications import load_profile, save_profile, add_approved_example

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

app = Flask(__name__)

FIELD_KEYWORDS = {
    "visa_status": ("visa", "sponsor", "authoriz", "citizenship", "work permit"),
    "salary_expectation": ("salary", "compensation", "pay expect"),
    "notice_period": ("notice",),
    "willing_to_relocate": ("relocat",),
    "linkedin": ("linkedin",),
    "github": ("github",),
}


def match_profile_field(question):
    q = question.lower()
    for field, keywords in FIELD_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return field
    return None


PAGE_HEAD = """<!doctype html>
<html><head><meta charset="utf-8"><title>Application Review</title>
<style>
  :root {
    --bg: #f7f7f8; --card: #ffffff; --border: #e5e5e7; --text: #1a1a1c; --muted: #6b6b70;
    --accent: #2563eb; --green: #16a34a; --green-dark: #15803d;
    --amber-bg: #fffbeb; --amber-border: #f3d38a; --amber-text: #92650c;
    --red-bg: #fef2f2; --red-border: #f3b7b7;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #16161a; --card: #202024; --border: #333338; --text: #ececec; --muted: #9a9aa0;
      --accent: #5b8cff; --green: #22c55e; --green-dark: #16a34a;
      --amber-bg: #2a2210; --amber-border: #6b5417; --amber-text: #f0c664;
      --red-bg: #2a1616; --red-border: #5c2b2b;
    }
  }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    background: var(--bg); color: var(--text);
    max-width: 760px; margin: 0 auto; padding: 0 20px 60px;
    line-height: 1.5;
  }
  header { position: sticky; top: 0; background: var(--bg); padding: 20px 0 14px; z-index: 10; }
  header h1 { font-size: 20px; margin: 0 0 4px; }
  .stats { color: var(--muted); font-size: 13px; }
  .disclaimer {
    font-size: 12.5px; color: var(--muted); background: var(--card); border: 1px solid var(--border);
    border-radius: 8px; padding: 8px 12px; margin-top: 10px;
  }
  .job {
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 18px 20px; margin-bottom: 16px;
  }
  .job-title { font-size: 16px; font-weight: 600; margin: 0; }
  .job-meta { color: var(--muted); font-size: 13px; margin: 3px 0 14px; display: flex; align-items: center; gap: 10px; }
  .job-meta a { color: var(--accent); text-decoration: none; font-weight: 500; }
  .job-meta a:hover { text-decoration: underline; }
  .field-label {
    display: block; font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.03em; color: var(--muted); margin: 14px 0 6px;
  }
  textarea, input[type=text] {
    width: 100%; font-family: inherit; font-size: 14px; color: var(--text);
    background: var(--bg); border: 1px solid var(--border); border-radius: 7px;
    padding: 9px 10px; resize: vertical;
  }
  textarea:focus, input[type=text]:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
  .flagged {
    background: var(--amber-bg); border: 1px solid var(--amber-border); border-radius: 8px;
    padding: 10px 12px; margin-top: 12px; font-size: 13.5px;
  }
  .flagged .q { color: var(--amber-text); font-weight: 600; }
  .flagged form { display: flex; gap: 6px; margin-top: 8px; }
  .flagged input[type=text] { flex: 1; background: var(--card); }
  .actions { margin-top: 16px; display: flex; gap: 8px; }
  button {
    padding: 8px 18px; border-radius: 7px; border: 1px solid var(--border);
    cursor: pointer; font-size: 13.5px; font-weight: 500; background: var(--card); color: var(--text);
  }
  button:hover { filter: brightness(0.96); }
  .approve { background: var(--green); color: white; border: none; }
  .approve:hover { background: var(--green-dark); filter: none; }
  .reject { color: var(--muted); }
  .save-answer { background: var(--accent); color: white; border: none; padding: 8px 14px; }
  footer { color: var(--muted); font-size: 12px; margin-top: 30px; text-align: center; }
  .empty { color: var(--muted); padding: 60px 0; text-align: center; font-size: 15px; }
  section + section { margin-top: 34px; padding-top: 6px; border-top: 1px solid var(--border); }
  section h2 { font-size: 15px; margin: 14px 0 12px; color: var(--muted); font-weight: 600; }
  .ats-tag {
    font-size: 11px; padding: 2px 7px; border-radius: 5px; border: 1px solid var(--border);
    color: var(--muted); text-transform: uppercase; letter-spacing: 0.03em;
  }
  .fill { background: var(--accent); color: white; border: none; }
  .submitted-btn { color: var(--muted); }
  .resume-box {
    margin-top: 12px; padding: 10px 12px; border-radius: 8px;
    background: var(--bg); border: 1px solid var(--border); font-size: 13px;
  }
  .resume-box a { color: var(--accent); font-weight: 600; text-decoration: none; }
  .resume-box a:hover { text-decoration: underline; }
  .score-up { color: var(--green); font-weight: 600; }
  .tailor-wait { font-size: 12px; color: var(--muted); margin-left: 8px; }
</style></head><body>
"""

TAILOR_SCRIPT = """
<script>
function tstart(btn) {
  var idx = btn.dataset.idx, startUrl = btn.dataset.start, statusUrl = btn.dataset.status;
  var actionsEl = document.getElementById('tailor-actions-' + idx);
  btn.remove();
  var wait = document.createElement('span');
  wait.className = 'tailor-wait';
  wait.textContent = 'Generating resume... usually 20-30s';
  actionsEl.appendChild(wait);
  fetch(startUrl, {method: 'POST'});
  tpoll(idx, statusUrl, wait, 0);
}

function tpoll(idx, statusUrl, waitEl, attempt) {
  fetch(statusUrl).then(function(r) { return r.json(); }).then(function(d) {
    var resultEl = document.getElementById('tailor-result-' + idx);
    if (d.ready) {
      if (waitEl) waitEl.remove();
      var html = '<a href="' + d.download_url + '">Download tailored resume &darr;</a>';
      if (d.score_original !== null && d.score_tailored !== null) {
        html += ' &middot; fit score: ' + d.score_original + '/100 &rarr; ' +
                '<span class="score-up">' + d.score_tailored + '/100</span>';
        if (d.score_reason) html += ' (' + d.score_reason + ')';
      }
      resultEl.innerHTML = html;
      resultEl.hidden = false;
      return;
    }
    if (d.failed) {
      if (waitEl) waitEl.remove();
      var actionsEl = document.getElementById('tailor-actions-' + idx);
      var retryBtn = document.createElement('button');
      retryBtn.type = 'button';
      retryBtn.className = 'tailor-btn';
      retryBtn.textContent = 'Generate Tailored Resume (~$0.06)';
      retryBtn.dataset.idx = idx;
      retryBtn.dataset.start = d.start_url;
      retryBtn.dataset.status = statusUrl;
      retryBtn.onclick = function() { tstart(retryBtn); };
      actionsEl.appendChild(retryBtn);
      resultEl.textContent = 'Last generation attempt failed to compile — try again.';
      resultEl.hidden = false;
      return;
    }
    if (attempt < 40) {
      setTimeout(function() { tpoll(idx, statusUrl, waitEl, attempt + 1); }, 3000);
    } else if (waitEl) {
      waitEl.textContent = 'Still working — refresh the page in a bit.';
    }
  }).catch(function() {
    if (attempt < 40) {
      setTimeout(function() { tpoll(idx, statusUrl, waitEl, attempt + 1); }, 3000);
    }
  });
}
</script>
"""


def render_page(apps, profile):
    pending = {jid: a for jid, a in apps.items() if a.get("status") == "pending"}
    approved = {jid: a for jid, a in apps.items() if a.get("status") == "approved"}
    n_answers = len(profile.get("answers", {}))
    n_examples = len(profile.get("approved_examples", []))
    n_flagged = sum(len(a.get("flagged_questions", [])) for a in pending.values())

    html = [PAGE_HEAD]
    html.append("<header>")
    html.append(f"<h1>{len(pending)} application{'s' if len(pending) != 1 else ''} to review</h1>")
    html.append(f"<div class='stats'>profile.json: {n_answers} standing answers &middot; "
                f"{n_examples} approved-style examples &middot; {n_flagged} questions still open &middot; "
                f"{len(approved)} approved, ready to apply</div>")
    html.append("<div class='disclaimer'>This page only drafts content — nothing is ever submitted to an "
                "employer. Approving just saves your final text; \"Open &amp; Fill\" opens a real browser and "
                "fills the form, but never clicks Submit — that part is always you.</div>")
    html.append("</header>")

    html.append("<section>")
    if not pending:
        html.append('<div class="empty">Nothing pending. Run <code>main.py</code> to generate more.</div>')
    else:
        for jid, a in pending.items():
            html.append('<div class="job">')
            html.append(f"<p class='job-title'>{escape(a['title'])}</p>")
            html.append(f"<div class='job-meta'><span>{escape(a['company'])}</span>"
                        f"<a href='{escape(a['apply_url'])}' target='_blank'>Apply link &rarr;</a></div>")
            html.append(f"<form method='post' action='{url_for('approve', job_id=jid)}'>")
            html.append("<span class='field-label'>Why this role</span>")
            html.append(f"<textarea name='why_this_role' rows='3'>{escape(a['why_this_role'])}</textarea>")
            for q, v in a.get("standard_answers", {}).items():
                html.append(f"<span class='field-label'>{escape(q)}</span>")
                html.append(f"<input type='text' name='sa__{escape(q)}' value='{escape(v)}'>")
            html.append("<div class='actions'>")
            html.append("<button type='submit' class='approve'>Approve</button>")
            html.append("</div></form>")
            html.append(f"<form method='post' action='{url_for('reject', job_id=jid)}' style='display:inline'>"
                        "<button type='submit' class='reject'>Reject / Skip</button></form>")

            for q in a.get("flagged_questions", []):
                html.append("<div class='flagged'>")
                html.append(f"<span class='q'>Unanswered:</span> {escape(q)}")
                html.append(f"<form method='post' action='{url_for('answer', job_id=jid)}'>")
                html.append(f"<input type='hidden' name='question' value='{escape(q)}'>")
                html.append("<input type='text' name='answer' placeholder='Your answer' required>")
                html.append("<button type='submit' class='save-answer'>Save</button>")
                html.append("</form></div>")

            html.append("</div>")
    html.append("</section>")

    html.append("<section>")
    html.append(f"<h2>{len(approved)} approved &mdash; ready to apply</h2>")
    if approved:
        for jid, a in approved.items():
            ats = detect_ats(a["apply_url"])
            dom_id = escape(jid)
            html.append('<div class="job">')
            html.append(f"<p class='job-title'>{escape(a['title'])} "
                        f"<span class='ats-tag'>{escape(ats)}</span></p>")
            html.append(f"<div class='job-meta'><span>{escape(a['company'])}</span>"
                        f"<a href='{escape(a['apply_url'])}' target='_blank'>Apply link &rarr;</a></div>")
            html.append(f"<div class='actions' id='tailor-actions-{dom_id}'>")
            if ats in ("greenhouse", "lever"):
                html.append(f"<form method='post' action='{url_for('open_apply', job_id=jid)}' style='display:inline'>"
                            "<button type='submit' class='fill'>Open &amp; Fill Application</button></form>")
            tailored = a.get("tailored_resume")
            tailored_ready = tailored and tailored.get("compiled") and os.path.exists(
                os.path.join(PROJECT_ROOT, tailored["pdf_path"]))
            tailored_failed = tailored and not tailored.get("compiled")
            if not tailored_ready:
                html.append(
                    f"<button type='button' class='tailor-btn' data-idx='{dom_id}' "
                    f"data-start='{url_for('tailor', job_id=jid)}' "
                    f"data-status='{url_for('tailor_status', job_id=jid)}' "
                    "onclick='tstart(this)'>Generate Tailored Resume (~$0.06)</button>"
                )
            html.append(f"<form method='post' action='{url_for('mark_submitted', job_id=jid)}' style='display:inline'>"
                        "<button type='submit' class='submitted-btn'>Mark as Submitted</button></form>")
            html.append("</div>")
            html.append(f"<div class='resume-box' id='tailor-result-{dom_id}'"
                        f"{'' if (tailored_ready or tailored_failed) else ' hidden'}>")
            if tailored_ready:
                so, st = tailored["score_original"], tailored["score_tailored"]
                html.append(f"<a href='{url_for('download_resume', job_id=jid)}'>Download tailored resume &darr;</a>")
                if so is not None and st is not None:
                    html.append(f" &middot; fit score: {so}/100 &rarr; "
                                f"<span class='score-up'>{st}/100</span>")
                    if tailored.get("score_reason"):
                        html.append(f" ({escape(tailored['score_reason'])})")
            elif tailored_failed:
                html.append("Last generation attempt failed to compile — try again.")
            html.append("</div>")
            html.append("</div>")
    html.append("</section>")

    html.append("<footer>Local-only, runs at 127.0.0.1:5000. Ctrl+C in the terminal to stop.</footer>")
    html.append(TAILOR_SCRIPT)
    html.append("</body></html>")
    return "\n".join(html)


@app.route("/")
def index():
    apps = load_applications()
    profile = load_profile()
    return render_page(apps, profile)


@app.route("/approve/<path:job_id>", methods=["POST"])
def approve(job_id):
    apps = load_applications()
    profile = load_profile()
    entry = apps.get(job_id)
    if not entry:
        return redirect(url_for("index"))

    why_this_role = request.form.get("why_this_role", entry["why_this_role"])

    updated_answers = {}
    for key, value in request.form.items():
        if key.startswith("sa__"):
            question = key[len("sa__"):]
            updated_answers[question] = value
            original = entry.get("standard_answers", {}).get(question)
            if value != original:
                field = match_profile_field(question)
                if field:
                    profile[field] = value
                else:
                    profile.setdefault("answers", {})[question] = value

    entry["why_this_role"] = why_this_role
    entry["standard_answers"] = updated_answers
    entry["status"] = "approved"

    add_approved_example(profile, entry["title"], why_this_role)

    save_profile(profile)
    save_applications(apps)
    return redirect(url_for("index"))


@app.route("/reject/<path:job_id>", methods=["POST"])
def reject(job_id):
    apps = load_applications()
    if job_id in apps:
        apps[job_id]["status"] = "rejected"
        save_applications(apps)
    return redirect(url_for("index"))


@app.route("/open_apply/<path:job_id>", methods=["POST"])
def open_apply(job_id):
    """Launches apply_to_job.py as a detached subprocess so this request returns
    immediately — the actual browser window opens and stays open independently,
    outside the Flask request/response lifecycle."""
    apps = load_applications()
    entry = apps.get(job_id)
    if entry and entry.get("status") == "approved":
        apply_script = os.path.join(SCRIPT_DIR, "apply_to_job.py")
        subprocess.Popen([sys.executable, apply_script, job_id], cwd=SCRIPT_DIR)
    return redirect(url_for("index"))


@app.route("/tailor/<path:job_id>", methods=["POST"])
def tailor(job_id):
    """Launches tailor_resume.py as a detached subprocess, same non-blocking
    pattern as open_apply — takes ~15-20s (Claude call + LaTeX compile + two
    scoring calls), so this returns immediately rather than making the page hang.
    Costs ~$0.06 real money per click; only ever runs when you click it."""
    apps = load_applications()
    if job_id in apps:
        tailor_script = os.path.join(SCRIPT_DIR, "tailor_resume.py")
        subprocess.Popen([sys.executable, tailor_script, job_id], cwd=SCRIPT_DIR)
    return redirect(url_for("index"))


@app.route("/tailor_status/<path:job_id>")
def tailor_status(job_id):
    """Polled by the page's JS every few seconds after a Generate click, so the
    button can flip to a download link + scores without a manual page refresh."""
    apps = load_applications()
    entry = apps.get(job_id)
    tailored = entry.get("tailored_resume") if entry else None
    if not tailored:
        return jsonify(ready=False, failed=False)

    pdf_ok = tailored.get("compiled") and os.path.exists(os.path.join(PROJECT_ROOT, tailored.get("pdf_path", "")))
    if pdf_ok:
        return jsonify(
            ready=True, failed=False,
            download_url=url_for("download_resume", job_id=job_id),
            score_original=tailored.get("score_original"),
            score_tailored=tailored.get("score_tailored"),
            score_reason=tailored.get("score_reason"),
        )
    return jsonify(ready=False, failed=True, start_url=url_for("tailor", job_id=job_id))


@app.route("/resume/<path:job_id>")
def download_resume(job_id):
    apps = load_applications()
    entry = apps.get(job_id)
    tailored = entry.get("tailored_resume") if entry else None
    if not tailored or not tailored.get("pdf_path"):
        return redirect(url_for("index"))
    pdf_path = os.path.join(PROJECT_ROOT, tailored["pdf_path"])
    if not os.path.exists(pdf_path):
        return redirect(url_for("index"))
    download_name = f"{entry['company']}_{entry['title']}.pdf".replace("/", "-")
    return send_file(pdf_path, as_attachment=True, download_name=download_name)


@app.route("/submitted/<path:job_id>", methods=["POST"])
def mark_submitted(job_id):
    """Purely a manual confirmation the user clicks after actually applying —
    never inferred or assumed by the system."""
    apps = load_applications()
    if job_id in apps:
        apps[job_id]["status"] = "submitted"
        save_applications(apps)
    return redirect(url_for("index"))


@app.route("/answer/<path:job_id>", methods=["POST"])
def answer(job_id):
    question = request.form.get("question", "").strip()
    user_answer = request.form.get("answer", "").strip()
    if not question or not user_answer:
        return redirect(url_for("index"))

    profile = load_profile()
    profile.setdefault("answers", {})[question] = user_answer
    save_profile(profile)

    apps = load_applications()
    entry = apps.get(job_id)
    if entry and question in entry.get("flagged_questions", []):
        entry["flagged_questions"].remove(question)
        save_applications(apps)

    return redirect(url_for("index"))


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Review server running at {url} (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=5000, debug=False)
