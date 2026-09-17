"""
Resume Tailor
Given the master resume LaTeX template and one job posting, asks Claude to
reword/reorder the EXISTING content (never invent new experience or projects)
to match that job's language, then compiles the result to PDF locally via
MiKTeX. The finished .pdf lands in resume/generated/<YYYY-MM-DD>/ (kept clean,
PDFs only — this is what you browse to download). The .tex/.aux/.log build
scrap goes into resume/_dump/<YYYY-MM-DD>/ instead, out of the way. Dated
subfolders mean a busy day doesn't overwrite the day before or collide with
itself.

On-demand only — this is never called from main.py's daily run (that would cost
~$56/month at 30 jobs/day; decided against it). You run this yourself, per job,
when you're actually about to apply.

Usage:
  python tailor_resume.py <job_id>     one specific job, by its exact id
  python tailor_resume.py tiktok       case-insensitive title/company search
                                        (errors and lists matches if ambiguous)
  python tailor_resume.py 3            dev/trial mode: first 3 jobs in the file
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime

import anthropic

from draft_applications import MODEL_PRICING
from applications_store import load_applications, save_applications
from rank_jobs import extract_resume_text

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
RESUME_TEX = os.path.join(PROJECT_ROOT, "resume", "resume_template.tex")
GENERATED_ROOT = os.path.join(PROJECT_ROOT, "resume", "generated")
# .tex/.aux/.log build scrap goes here, never into GENERATED_ROOT — that folder
# should only ever contain the finished .pdf files a human will browse/download.
DUMP_ROOT = os.path.join(PROJECT_ROOT, "resume", "_dump")

# Hardcoded per this machine's MiKTeX user-mode install (see README "Resume tailoring").
PDFLATEX = r"C:\Users\spraj\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdflatex.exe"


def slugify(text, max_len=40):
    text = re.sub(r"[^\w\s-]", "", text).strip().replace(" ", "-")
    return text[:max_len].rstrip("-") or "untitled"


def load_resume_tex():
    with open(RESUME_TEX, "r", encoding="utf-8") as f:
        return f.read()


def tailor_tex(resume_tex, job, config):
    api_key = config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or "your-key" in api_key:
        raise RuntimeError("anthropic_api_key not set in config.json")

    model = config.get("draft_model") or config.get("anthropic_model", "claude-haiku-4-5")
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = (
        "You tailor a LaTeX resume to a specific job posting. You will be given the complete "
        ".tex source and a job posting. Return the COMPLETE modified .tex source and nothing else "
        "— no markdown fences, no explanation before or after, no commentary. The response must "
        "compile as-is with pdflatex.\n\n"
        "HARD RULES:\n"
        "1. Never invent experience, skills, projects, employers, or achievements. You may only "
        "reword, reorder, re-emphasize, or trim content that is already present in the given .tex. "
        "If a detail isn't in the source document, it does not go in the output.\n"
        "2. Do not add or remove entire sections, experience entries, or projects. Do not change "
        "dates, employer names, degree names, or contact information.\n"
        "3. You may reword bullet text, reorder which bullets come first within an entry, and "
        "adjust the Summary section's wording — using only facts already stated elsewhere in the "
        "document.\n"
        "4. Preserve every LaTeX command, environment, and package exactly (\\begin{{...}}/\\end{{...}} "
        "pairs, \\textbf, \\href, \\vspace, etc.) — only the human-readable text content changes. "
        "Broken LaTeX syntax is a failure.\n"
        "5. Total content length must stay close to the original — this must still fit on one "
        "page when compiled. Trim wording if a rewording would run long; do not let it grow.\n\n"
        "WRITING STYLE — apply to every sentence you write:\n"
        "- Never use a hyphen or dash for any reason: not to join two clauses (no 'X — Y', no 'X - Y'), "
        "and not inside a compound word either. Write 'decision making' not 'decision-making', 'data "
        "driven' not 'data-driven', 'end to end' not 'end-to-end'. The only exception is a proper noun "
        "already spelled with a hyphen in the source resume (a product or technology name) — leave that "
        "exact spelling alone.\n"
        "- No AI-sounding vocabulary: leverage, delve, tapestry, realm, robust, seamless, cutting edge, "
        "unlock, elevate, game changer, dynamic, innovative, passionate, furthermore, moreover, in "
        "today's, holistic, synergy, arena, arsenal, bombard, captivate, catapult, fast paced, foster, "
        "harness, navigate, revolutionary, skyrocket, supercharge, embark, deep dive, drive impact, "
        "strategic alignment, operational excellence, continuous improvement.\n"
        "- Never open with a cliche framing device ('in today's fast-paced world', 'in a world where'). "
        "Start with a concrete fact.\n"
        "- Never use AI sentence-pattern tells: 'It's not about X, it's about Y', three short punchy "
        "declaratives in a row ('X. Y. Z.'), or a rhetorical question answered right after ('And the "
        "result? Significant.').\n"
        "- Be crisp. No restating, no previewing, no filler. No hedging ('generally speaking', 'to some "
        "extent').\n"
        "- Vary sentence length and opening word.\n"
        "- Prefer concrete nouns (an actual tool or technology) over vague qualifiers."
    )
    user_msg = (
        f"JOB POSTING:\nTitle: {job['title']}\nCompany: {job['company']}\n"
        f"Description: {job.get('description', '')[:2500]}\n\n"
        f"CURRENT RESUME (.tex):\n{resume_tex}"
    )

    response = client.messages.create(
        model=model,
        max_tokens=8000,
        thinking={"type": "disabled"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )

    usage = response.usage
    input_rate, output_rate = MODEL_PRICING.get(model, MODEL_PRICING["claude-haiku-4-5"])
    cost = usage.input_tokens / 1_000_000 * input_rate + usage.output_tokens / 1_000_000 * output_rate

    raw = next(block.text for block in response.content if block.type == "text").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw).rstrip("`").strip()

    return raw, cost


def score_resume_fit(resume_text, job, config):
    """Cheap Haiku call, same spirit as rank_jobs.py's scoring: how well does this
    resume (as plain extracted text) fit this one job, 0-100. Always Haiku regardless
    of draft_model — this is a scoring/classification task, not a writing task."""
    api_key = config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = (
        "You score how well a resume fits one specific job posting, on genuine overlap between "
        "the resume's real skills/experience and the job's actual stated requirements — not on "
        "how well-written the resume is. Return ONLY a JSON object, no prose: "
        '{"score": <0-100 int>, "reason": "<one short clause>"}.'
    )
    user_msg = (
        f"JOB:\nTitle: {job['title']}\nCompany: {job['company']}\n"
        f"Description: {job.get('description', '')[:2000]}\n\n"
        f"RESUME TEXT:\n{resume_text[:4000]}"
    )
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )
    usage = response.usage
    input_rate, output_rate = MODEL_PRICING["claude-haiku-4-5"]
    cost = usage.input_tokens / 1_000_000 * input_rate + usage.output_tokens / 1_000_000 * output_rate
    raw = next(block.text for block in response.content if block.type == "text").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(json)?", "", raw).rstrip("`").strip()
    data = json.loads(raw)
    return data.get("score", 0), data.get("reason", ""), cost


def compile_tex(tex_path, output_dir, jobname):
    result = subprocess.run(
        [PDFLATEX, "-interaction=nonstopmode", "-halt-on-error",
         f"-output-directory={output_dir}", f"-jobname={jobname}", tex_path],
        capture_output=True, text=True, timeout=60,
    )
    pdf_path = os.path.join(output_dir, f"{jobname}.pdf")
    if result.returncode != 0 or not os.path.exists(pdf_path):
        return False, result.stdout[-2000:]
    return True, None


def tailor_and_compile(job, resume_tex, output_dir, dump_dir, config):
    slug = f"{slugify(job['company'])}_{slugify(job['title'])}"
    tex_path = os.path.join(dump_dir, f"{slug}.tex")
    pdf_path = os.path.join(output_dir, f"{slug}.pdf")

    tailored_tex, cost = tailor_tex(resume_tex, job, config)
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tailored_tex)

    # pdflatex writes .pdf/.aux/.log together into dump_dir (scrap); only the
    # finished .pdf then gets copied into output_dir (the clean, browsable folder).
    ok, error = compile_tex(tex_path, dump_dir, slug)
    if ok:
        import shutil
        shutil.copyfile(os.path.join(dump_dir, f"{slug}.pdf"), pdf_path)

    result = {
        "job_id": job.get("id") or job.get("job_id"),
        "title": job["title"],
        "company": job["company"],
        "slug": slug,
        "pdf_path": pdf_path,
        "cost": cost,
        "compiled": ok,
        "error": error,
        "score_original": None,
        "score_tailored": None,
        "score_reason": None,
    }

    if ok:
        original_pdf = os.path.join(PROJECT_ROOT, "resume", "resume.pdf")
        original_text = extract_resume_text(original_pdf)
        tailored_text = extract_resume_text(pdf_path)
        score_o, _, cost_o = score_resume_fit(original_text, job, config)
        score_t, reason_t, cost_t = score_resume_fit(tailored_text, job, config)
        result["cost"] += cost_o + cost_t
        result["score_original"] = score_o
        result["score_tailored"] = score_t
        result["score_reason"] = reason_t

    return result


def record_tailoring_result(result):
    """Persist the outcome into applications.json so review_server.py can show a
    download link + score without re-running anything. PDF path stored relative
    to PROJECT_ROOT so it stays portable if the project folder ever moves."""
    apps = load_applications()
    job_id = result["job_id"]
    if job_id not in apps:
        return
    apps[job_id]["tailored_resume"] = {
        "pdf_path": os.path.relpath(result["pdf_path"], PROJECT_ROOT).replace("\\", "/"),
        "compiled": result["compiled"],
        "score_original": result["score_original"],
        "score_tailored": result["score_tailored"],
        "score_reason": result["score_reason"],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_applications(apps)


def find_jobs(selector):
    """Resolve a CLI selector to a list of jobs. Three modes:
    - exact job_id (contains ':', matching the id scheme used everywhere else)
    - a plain integer N -> the first N jobs in applications.json (dev/trial use)
    - anything else -> case-insensitive substring match against title/company
      (the realistic on-demand path: `python tailor_resume.py tiktok`)
    """
    apps = load_applications()

    if selector in apps:
        return [dict(apps[selector], id=selector)]

    if selector.isdigit():
        n = int(selector)
        return [dict(job, id=job_id) for job_id, job in list(apps.items())[:n]]

    needle = selector.lower()
    matches = [
        dict(job, id=job_id) for job_id, job in apps.items()
        if needle in job["title"].lower() or needle in job["company"].lower()
    ]
    if len(matches) > 1:
        print(f"'{selector}' matches {len(matches)} jobs — be more specific:")
        for m in matches:
            print(f"  {m['id']}  —  {m['title']} @ {m['company']}")
        return []
    return matches


def run(jobs):
    resume_tex = load_resume_tex()
    today = datetime.now().strftime("%Y-%m-%d")
    output_dir = os.path.join(GENERATED_ROOT, today)
    dump_dir = os.path.join(DUMP_ROOT, today)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(dump_dir, exist_ok=True)

    with open(os.path.join(PROJECT_ROOT, "config.json"), encoding="utf-8") as f:
        config = json.load(f)

    total_cost = 0.0
    failed = 0
    print(f"Tailoring resumes for {len(jobs)} job(s) -> {output_dir}\n")
    for job in jobs:
        print(f"--- {job['title']} @ {job['company']} ---")
        try:
            result = tailor_and_compile(job, resume_tex, output_dir, dump_dir, config)
        except Exception as e:
            failed += 1
            print(f"  FAILED ({e.__class__.__name__}: {e}) — skipping this job, continuing with the rest\n")
            continue
        total_cost += result["cost"]
        status = "OK" if result["compiled"] else "COMPILE FAILED"
        print(f"  {status} — {result['slug']}.pdf — ~${result['cost']:.4f}")
        if result["job_id"]:
            record_tailoring_result(result)
        if result["compiled"]:
            print(f"  Fit score — original: {result['score_original']}/100, "
                  f"tailored: {result['score_tailored']}/100 ({result['score_reason']})")
        else:
            print(f"  Error tail:\n{result['error']}")
        print()

    print(f"Total cost this run: ~${total_cost:.4f}" + (f" ({failed} job(s) failed and were skipped)" if failed else ""))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    jobs = find_jobs(sys.argv[1])
    if jobs:
        run(jobs)
