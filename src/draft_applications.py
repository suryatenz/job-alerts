"""
Application Drafter
One batched Claude call per run: given today's top-N ranked jobs (already carry
full descriptions from rank_jobs.py) plus the growing profile.json, drafts a
tailored "why this role" note and fills in standard application answers.
Anything the profile doesn't cover yet gets flagged for the review page.

This ONLY drafts content — it never fills out or submits a real application form.
"""

import json
import os
import re

import anthropic

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
PROFILE_FILE = os.path.join(PROJECT_ROOT, "profile", "profile.json")
MAX_APPROVED_EXAMPLES = 5

# ($ per 1M input tokens, $ per 1M output tokens) — used for accurate cost logging
# regardless of which model draft_model/anthropic_model points at.
MODEL_PRICING = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def load_profile():
    if not os.path.exists(PROFILE_FILE):
        raise FileNotFoundError(
            f"profile.json not found at {PROFILE_FILE}. Copy profile/profile.example.json to profile/profile.json and fill it in."
        )
    with open(PROFILE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_profile(profile):
    with open(PROFILE_FILE, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=4)


def add_approved_example(profile, job_title, why_this_role):
    """Called by review_server.py when a draft is approved — keeps the last N
    real approved examples as free few-shot anchors for future drafting calls."""
    examples = profile.setdefault("approved_examples", [])
    examples.append({"job_title": job_title, "why_this_role": why_this_role})
    profile["approved_examples"] = examples[-MAX_APPROVED_EXAMPLES:]


def _profile_summary(profile):
    lines = [
        f"Full name: {profile.get('full_name', '')}",
        f"Email: {profile.get('email', '')}",
        f"Phone: {profile.get('phone', '')}",
        f"LinkedIn: {profile.get('linkedin', '')}",
        f"GitHub: {profile.get('github', '')}",
        f"Visa/work authorization status: {profile.get('visa_status', '')}",
        f"Notice period: {profile.get('notice_period', '') or 'not yet specified'}",
        f"Salary expectation: {profile.get('salary_expectation', '') or 'not yet specified'}",
        f"Willing to relocate: {profile.get('willing_to_relocate', '') or 'not yet specified'}",
    ]
    answers = profile.get("answers", {})
    if answers:
        lines.append("\nStanding answers to previously-seen questions:")
        for q, a in answers.items():
            lines.append(f"  Q: {q}\n  A: {a}")

    examples = profile.get("approved_examples", [])
    if examples:
        lines.append("\nExamples of previously APPROVED 'why this role' notes (match this tone/length):")
        for ex in examples:
            lines.append(f"  For \"{ex['job_title']}\": {ex['why_this_role']}")

    return "\n".join(lines)


def generate_drafts(jobs, profile, config):
    """Returns (drafts, cost) where drafts is a list of dicts:
    {job_id, why_this_role, standard_answers, flagged_questions}"""
    if not jobs:
        return [], 0.0

    api_key = config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or "your-key" in api_key:
        raise RuntimeError("anthropic_api_key not set in config.json")

    # Drafting is a writing task, not a scoring task — worth a stronger model than
    # ranking uses. Configurable separately (draft_model) so ranking can stay on
    # cheap Haiku while drafting uses something better, independently of each other.
    model = config.get("draft_model") or config.get("anthropic_model", "claude-haiku-4-5")
    client = anthropic.Anthropic(api_key=api_key)

    listing = "\n".join(
        f"{i}. {j['title']} | {j['company']} | {j['location']}"
        + (f"\n   About: {j['description'][:1200]}" if j.get("description") else "")
        for i, j in enumerate(jobs)
    )

    system_prompt = (
        "You draft job application content for a candidate, given their profile and a list of job postings. "
        "This is DRAFT content only — it will be reviewed and edited by the candidate before anything is submitted "
        "anywhere, so it is fine to make reasonable best-effort attempts rather than leaving things blank. "
        "Return ONLY a JSON array, no prose, no markdown fences. Each element:\n"
        '{"index": <int>, "why_this_role": "<2-3 tailored sentences connecting the candidate\'s actual '
        'background to this specific role/company>", '
        '"standard_answers": {"<question>": "<answer>", ...}, '
        '"flagged_questions": ["<question>", ...]}\n\n'
        "For standard_answers: include entries for whichever of these the posting's description actually asks about "
        "and the profile has a real answer for — work authorization/visa sponsorship, notice period, salary "
        "expectation, willingness to relocate, LinkedIn/GitHub/portfolio links. Use the profile's standing answers "
        "verbatim when a question matches one already answered, even if worded slightly differently. "
        "IMPORTANT: every key in standard_answers must be phrased as an actual question sentence, in the wording "
        "an application form would realistically use (e.g. 'Are you legally authorized to work in the United "
        "States?', 'Do you now or will you in the future require visa sponsorship?') — never a short internal "
        "label or snake_case identifier (e.g. NOT 'work_authorization', NOT 'visa_status'). This text is used "
        "later to find the matching field on the real form by its on-page label, so it must read the way a "
        "human-written form label reads, not like a database column name.\n\n"
        "For flagged_questions: list ONLY questions the posting's description explicitly asks (e.g. 'do you have "
        "a portfolio', 'are you willing to work weekends') that the profile does NOT already answer — either in its "
        "structured fields or its standing answers. Do not invent questions the posting doesn't actually ask. If "
        "nothing is unanswered, return an empty list.\n\n"
        "Never fabricate factual claims not supported by the candidate's actual background — no invented "
        "employers, skills, or achievements. If the posting explicitly requires something the candidate's "
        "profile has no answer for and it's an EEO/demographic self-identification question (race, gender, "
        "disability, veteran status), do not guess — put it in flagged_questions instead.\n\n"
        "Do NOT speculate about the employer's visa sponsorship practices, immigration policies, or claim a "
        "location is 'visa-friendly' or has 'sponsorship infrastructure' — we do not have real data on any "
        "specific company's sponsorship track record, and stating this as fact would be misleading if reused "
        "in a real application. why_this_role must stay strictly about the fit between the candidate's actual "
        "skills/experience/interests and the role's actual responsibilities — never mention sponsorship, visa "
        "status, or immigration there at all. The candidate's own visa/work-authorization status belongs only "
        "in standard_answers, stated as a plain fact about the candidate, never dressed up with claims about "
        "the employer's policies.\n\n"
        "WRITING STYLE — apply this to every sentence you write, no exceptions:\n"
        "1. Stay tight to the given resume and job description. Do not add generic industry commentary, "
        "career-advice framing, or claims that reach beyond what the resume and posting actually say.\n"
        "2. Never use a hyphen or dash for any reason — not to join two clauses (no 'X — Y', no 'X - Y'), and "
        "not inside a compound word either. Write 'decision making' not 'decision-making', 'data driven' not "
        "'data-driven', 'end to end' not 'end-to-end'. The only exception is a proper noun that is already "
        "spelled with a hyphen in the source resume (a product or technology name) — leave that exact spelling "
        "alone; otherwise split the words apart or rephrase.\n"
        "3. Do not use AI-sounding vocabulary. Banned words/phrases include: leverage, delve, tapestry, realm, "
        "robust, seamless, cutting edge, unlock, elevate, game changer, dynamic, innovative, passionate, "
        "thrilled, excited, furthermore, moreover, in today's, it's worth noting, at the end of the day, "
        "holistic, synergy, arena, arsenal, bombard, captivate, catapult, fast paced, foster, harness, "
        "navigate, revolutionary, skyrocket, supercharge, embark, deep dive, drive impact, unlock value, "
        "strategic alignment, operational excellence, continuous improvement.\n"
        "4. Never open with a cliche framing device: 'in today's fast-paced world', 'in a world where', "
        "'picture this', 'imagine a world'. Start with a concrete fact instead.\n"
        "5. Never use these AI sentence-pattern tells: 'It's not about X, it's about Y', 'Not because X, but "
        "because Y', 'That's not X, that's Y', three short punchy declaratives in a row ('X. Y. Z.'), or a "
        "rhetorical question answered in the next sentence ('And the result? Significant.').\n"
        "6. Be crisp. Do not restate the question before answering it, do not preview what you're about to say, "
        "do not pad with filler clauses. State the fact and stop.\n"
        "7. Vary sentence length and opening word. Never start two consecutive sentences the same way.\n"
        "8. No exclamation points, no rhetorical questions, no hedging ('I believe', 'I think', 'generally "
        "speaking', 'to some extent') — state things as plain fact.\n"
        "9. Avoid rule-of-three adjective lists ('innovative, dynamic, and forward-thinking'). One precise word "
        "beats three vague ones.\n"
        "10. Prefer concrete nouns (an actual tool, technology, or project name) over vague qualifiers like "
        "'various', 'multiple', or 'a range of'."
    )
    user_msg = (
        f"CANDIDATE PROFILE:\n{_profile_summary(profile)}\n\n"
        f"JOB POSTINGS (index. title | company | location):\n{listing}"
    )

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        thinking={"type": "disabled"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )

    usage = response.usage
    input_rate, output_rate = MODEL_PRICING.get(model, MODEL_PRICING["claude-haiku-4-5"])
    cost = usage.input_tokens / 1_000_000 * input_rate + usage.output_tokens / 1_000_000 * output_rate
    print(
        f"Claude usage (drafting, {model}): {usage.input_tokens} input + {usage.output_tokens} output tokens "
        f"(~${cost:.4f} this run)"
    )

    # Opus 5 runs adaptive thinking by default, which prepends a ThinkingBlock to
    # response.content — find the actual text block instead of assuming index 0.
    raw = next(block.text for block in response.content if block.type == "text").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(json)?", "", raw).rstrip("`").strip()
    entries = json.loads(raw)

    drafts = []
    for entry in entries:
        idx = entry.get("index")
        if idx is None or idx < 0 or idx >= len(jobs):
            continue
        job = jobs[idx]
        drafts.append({
            "job_id": job["id"],
            "title": job["title"],
            "company": job["company"],
            "apply_url": job["apply_url"],
            "description": job.get("description", ""),
            "why_this_role": entry.get("why_this_role", ""),
            "standard_answers": entry.get("standard_answers", {}),
            "flagged_questions": entry.get("flagged_questions", []),
        })

    return drafts, cost


if __name__ == "__main__":
    # quick manual test — run with `python draft_applications.py`
    # fetches + ranks real jobs the same way main.py does, then drafts for them
    from fetch_jobs import fetch_all_jobs
    from rank_jobs import get_top_jobs

    with open(os.path.join(PROJECT_ROOT, "config.json"), encoding="utf-8") as f:
        _config = json.load(f)

    _jobs = fetch_all_jobs(_config)
    _top_jobs, _rank_cost = get_top_jobs(_jobs, _config)
    print(f"\nDrafting for {len(_top_jobs)} ranked jobs...\n")

    _profile = load_profile()
    _drafts, _draft_cost = generate_drafts(_top_jobs, _profile, _config)

    for d in _drafts:
        print(f"--- {d['title']} @ {d['company']} ---")
        print("Why:", d["why_this_role"])
        print("Standard answers:", d["standard_answers"])
        print("Flagged questions:", d["flagged_questions"])
        print()

    print(f"Total cost this test: ~${_rank_cost + _draft_cost:.4f}")
