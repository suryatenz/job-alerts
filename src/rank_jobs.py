"""
Job Ranker
Pre-filters raw postings by title keywords (free), then sends the shortlist
to Claude in one batched call to score fit against the resume (cheap — a few
cents/month on Haiku). Skips jobs already sent on a previous day.
"""

import json
import os
import re

import anthropic
import PyPDF2

from fetch_jobs import enrich_descriptions
from draft_applications import MODEL_PRICING

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SEEN_FILE = os.path.join(PROJECT_ROOT, "data", "seen_jobs.json")
MAX_SEEN_HISTORY = 3000  # trim seen_jobs.json once it grows past this many ids

os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
NOISE_EMAIL_PATTERNS = ("noreply", "no-reply", "example.com", "sentry", "wixpress")


def extract_contact_email(description):
    """A posting occasionally names a contact ('questions? email jane@company.com').
    Most never do — this is a free, best-effort extraction, not a guarantee."""
    for match in EMAIL_RE.findall(description or ""):
        if not any(noise in match.lower() for noise in NOISE_EMAIL_PATTERNS):
            return match
    return None


def extract_resume_text(resume_path):
    text = []
    with open(resume_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text.append(page.extract_text() or "")
    return "\n".join(text)


def load_seen_ids():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(json.load(f))


def save_seen_ids(seen_ids):
    ids = list(seen_ids)
    if len(ids) > MAX_SEEN_HISTORY:
        ids = ids[-MAX_SEEN_HISTORY:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f)


SENIOR_TITLE_RE = re.compile(
    r"\b(senior|sr\.?|staff|principal|director|vp|vice president|head of|chief|lead)\b",
    re.IGNORECASE,
)

EXPERIENCE_RE = re.compile(
    r"(\d{1,2})(?:\s*(?:-|to)\s*\d{1,2})?\+?\s*years?\s+(?:of\s+)?"
    r"(?:related\s+|relevant\s+|professional\s+|industry\s+|work\s+)*experience",
    re.IGNORECASE,
)


def extract_min_years_required(description):
    """Best-effort read of the stated experience floor (e.g. '3+ years experience'
    -> 3, '0-2 years' -> 0). Takes the lowest floor mentioned, giving benefit of
    the doubt when a posting lists multiple experience bars. Free, local, no API
    call — just plain-text regex over the description we already fetched."""
    matches = EXPERIENCE_RE.findall(description or "")
    if not matches:
        return None
    return min(int(m) for m in matches)


NO_SPONSORSHIP_RE = re.compile(
    r"(?:no|not|without|unable to|cannot|will not|won'?t|does not|doesn'?t)\s+"
    r"(?:provide|offer|able to provide|able to offer)?\s*(?:visa\s+)?sponsor(?:ship)?",
    re.IGNORECASE,
)
CITIZEN_ONLY_RE = re.compile(
    r"(?:must be|only|require[sd]?|need to be)\s+(?:a\s+)?"
    r"(?:u\.?s\.?|united states)\s+citizen",
    re.IGNORECASE,
)


def detect_sponsorship_from_text(description):
    """Free, local scan of description text for explicit negative sponsorship
    language. Only ever returns 'no' or 'unknown' — postings almost never state
    a positive ('we sponsor') in plain text, so there's no reliable 'yes' signal
    to extract this way (that comes from SimplifyJobs' structured field instead)."""
    text = description or ""
    if NO_SPONSORSHIP_RE.search(text) or CITIZEN_ONLY_RE.search(text):
        return "no"
    return "unknown"


def is_phd_only(required_degrees):
    """SimplifyJobs-sourced postings sometimes list required_degrees explicitly.
    Only exclude when the posting is PhD-exclusive — anything that also accepts
    Bachelor's/Master's still fits an MS student."""
    return required_degrees == ["PhD"]


def prefilter_by_title(jobs, titles, include_internships):
    """Cheap keyword pre-filter before anything touches the Claude API. Also drops
    senior/staff/director-tier titles locally (free) instead of paying to have
    Claude read and then downscore them — cuts what reaches the paid call while
    also cutting noise a current MS student isn't eligible for anyway."""
    title_patterns = [re.escape(t.lower()) for t in titles]
    keep = []
    dropped_senior = 0
    for j in jobs:
        title_lower = j["title"].lower()
        if any(re.search(p, title_lower) for p in title_patterns):
            if not include_internships and "intern" in title_lower:
                continue
            if SENIOR_TITLE_RE.search(title_lower):
                dropped_senior += 1
                continue
            keep.append(j)
    if dropped_senior:
        print(f"Dropped {dropped_senior} senior/staff/director-tier postings before the API call")
    return keep


def dedupe_same_role(jobs):
    """Collapse postings that are the same title at the same company opened in
    multiple cities (e.g. one role posted separately for 14 locations) down to
    one representative — cuts near-duplicate tokens from the Claude call and
    stops one employer from filling the digest with copies of one role."""
    groups = {}
    order = []
    for j in jobs:
        key = (j["company"].lower(), j["title"].strip().lower())
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(j)

    deduped = []
    collapsed_count = 0
    for key in order:
        group = groups[key]
        rep = dict(group[0])
        if len(group) > 1:
            collapsed_count += len(group) - 1
            other_locations = [g["location"] for g in group[1:] if g["location"] != rep["location"]]
            if other_locations:
                shown = ", ".join(other_locations[:3])
                more = f" +{len(other_locations) - 3} more" if len(other_locations) > 3 else ""
                rep["location"] = f"{rep['location']} (also: {shown}{more})"
        deduped.append(rep)

    if collapsed_count:
        print(f"Collapsed {collapsed_count} same-role/same-company duplicate postings before the API call")
    return deduped


def rank_with_claude(jobs, resume_text, config):
    """One batched Claude call: score every pre-filtered job against the resume."""
    api_key = config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or "your-key" in api_key:
        raise RuntimeError("anthropic_api_key not set in config.json")

    model = config.get("anthropic_model", "claude-haiku-4-5")
    client = anthropic.Anthropic(api_key=api_key)

    # index, title, company, location, plus a trimmed description when we have one
    listing = "\n".join(
        f"{i}. {j['title']} | {j['company']} | {j['location']}"
        + (f"\n   About: {j['description'][:600]}" if j.get("description") else "")
        for i, j in enumerate(jobs)
    )

    system_prompt = (
        "You score job postings for fit against a candidate's resume. "
        "The candidate is an F1 international student, a current MS student with roughly 9-11 months of "
        "total internship experience (no full-time professional experience) — treat them as a genuine "
        "fresher/entry-level/new-grad candidate, not someone with 1-2+ years to offer. "
        "Return ONLY a JSON array, no prose, no markdown fences. "
        "Each element: {\"index\": <int>, \"score\": <0-100 int>, \"reason\": \"<max 8 words>\"}. "
        "Keep every reason to 8 words or fewer. "
        "Score on relevance of the job title/domain to the candidate's actual skills and experience level — "
        "favor Data Science / AI / ML / Data Analyst / BI roles matching their background, "
        "heavily penalize any role that reads as an established-IC or higher bar (expects a proven track record, "
        "deep expertise, or several years of ownership) even when no explicit year count is given — "
        "the obvious explicit cases are already filtered out before you see this list, so treat anything "
        "still here that implies seniority through language rather than a number just as harshly, "
        "penalize postings whose description implies no visa sponsorship / must be a citizen / no work authorization support "
        "(most explicit cases are already filtered out before you see this list — this is a backstop for subtler phrasing), "
        "and penalize roles clearly unrelated to data/software (e.g. sales, legal, physical labor)."
    )
    user_msg = (
        f"RESUME:\n{resume_text[:4000]}\n\n"
        f"JOB POSTINGS (index. title | company | location):\n{listing}"
    )

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )

    usage = response.usage
    input_rate, output_rate = MODEL_PRICING.get(model, MODEL_PRICING["claude-haiku-4-5"])
    cost = usage.input_tokens / 1_000_000 * input_rate + usage.output_tokens / 1_000_000 * output_rate
    print(
        f"Claude usage ({model}): {usage.input_tokens} input + {usage.output_tokens} output tokens "
        f"(~${cost:.4f} this run)"
    )

    # Some models (e.g. Opus 5) run adaptive thinking by default, which prepends a
    # ThinkingBlock to response.content — find the actual text block, don't assume index 0.
    raw = next(block.text for block in response.content if block.type == "text").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(json)?", "", raw).rstrip("`").strip()
    scores = json.loads(raw)

    scored_jobs = []
    for entry in scores:
        idx = entry.get("index")
        if idx is None or idx < 0 or idx >= len(jobs):
            continue
        job = dict(jobs[idx])
        job["score"] = entry.get("score", 0)
        job["reason"] = entry.get("reason", "")
        scored_jobs.append(job)

    scored_jobs.sort(key=lambda j: j["score"], reverse=True)
    return scored_jobs, cost


def fallback_rank(jobs, titles):
    """Used if the Claude call fails (e.g. no API credit) so the digest still sends.
    Scores by how many target titles a posting's title matches — cruder than AI
    ranking but keeps the pipeline working without the API."""
    title_patterns = [t.lower() for t in titles]
    scored = []
    for j in jobs:
        title_lower = j["title"].lower()
        hits = sum(1 for t in title_patterns if t in title_lower)
        job = dict(j)
        job["score"] = hits
        job["reason"] = "keyword match only (AI ranking unavailable)"
        scored.append(job)
    scored.sort(key=lambda j: j["score"], reverse=True)
    return scored


def get_top_jobs(all_jobs, config):
    resume_path = os.path.join(PROJECT_ROOT, config.get("resume_path", "resume/resume.pdf"))
    resume_text = extract_resume_text(resume_path)

    seen_ids = load_seen_ids()
    unseen_jobs = [j for j in all_jobs if j["id"] not in seen_ids]
    print(f"Skipping {len(all_jobs) - len(unseen_jobs)} already-seen postings")

    candidates = prefilter_by_title(
        unseen_jobs, config.get("titles", []), config.get("include_internships", True)
    )
    print(f"Title pre-filter: {len(candidates)} candidates out of {len(unseen_jobs)} unseen postings")

    if not candidates:
        return [], 0.0

    candidates = dedupe_same_role(candidates)

    # cap how many go to Claude in one call to keep the prompt (and cost) bounded
    candidates = candidates[:300]

    print(f"Fetching full descriptions for {len(candidates)} shortlisted postings...")
    candidates = enrich_descriptions(candidates)
    for j in candidates:
        j["contact_email"] = extract_contact_email(j.get("description", ""))
        j["min_years_required"] = extract_min_years_required(j.get("description", ""))
        # SimplifyJobs entries already carry a structured 'sponsorship' field from
        # the source's own tagging; every other source gets a text-based best-effort scan.
        if "sponsorship" not in j:
            j["sponsorship"] = detect_sponsorship_from_text(j.get("description", ""))

    max_years = config.get("max_years_experience", 1)
    over_experienced = [j for j in candidates if j["min_years_required"] is not None and j["min_years_required"] > max_years]
    candidates = [j for j in candidates if j not in over_experienced]
    if over_experienced:
        print(f"Dropped {len(over_experienced)} postings requiring more than {max_years}+ years (stated in description):")
        for j in over_experienced:
            print(f"  [{j['min_years_required']}+ yrs] {j['title']} - {j['company']}")

    no_sponsorship = [j for j in candidates if j.get("sponsorship") == "no"]
    candidates = [j for j in candidates if j.get("sponsorship") != "no"]
    if no_sponsorship:
        print(f"Dropped {len(no_sponsorship)} postings with no visa sponsorship / citizens-only (F1 status disqualifies):")
        for j in no_sponsorship:
            print(f"  [no sponsorship] {j['title']} - {j['company']}")

    phd_only = [j for j in candidates if is_phd_only(j.get("required_degrees", []))]
    candidates = [j for j in candidates if not is_phd_only(j.get("required_degrees", []))]
    if phd_only:
        print(f"Dropped {len(phd_only)} PhD-only postings (MS student doesn't qualify):")
        for j in phd_only:
            print(f"  [PhD only] {j['title']} - {j['company']}")

    cost = 0.0
    try:
        scored, cost = rank_with_claude(candidates, resume_text, config)
    except Exception as e:
        print(f"WARN: Claude ranking failed ({e}) - falling back to keyword-only ranking")
        scored = fallback_rank(candidates, config.get("titles", []))

    top_n = config.get("max_jobs_per_day", 20)
    top_jobs = scored[:top_n]

    seen_ids.update(j["id"] for j in top_jobs)
    save_seen_ids(seen_ids)

    return top_jobs, cost
