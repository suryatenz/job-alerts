"""
Job Fetcher
Pulls open postings from SimplifyJobs (new-grad + internships, community-maintained,
purpose-built for this), Greenhouse, Lever, RemoteOK, and (optionally) Adzuna, and
normalizes them all to a common shape:
    {id, title, company, location, apply_url, source, description}
SimplifyJobs entries additionally carry 'sponsorship' ('yes'/'no'/'unknown', from
its own structured field) and 'required_degrees' (list) — every other source gets
'sponsorship' filled in later by rank_jobs.py via a text scan of the description.
"""

import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (job-alerts personal script)"}
TIMEOUT = 15

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
COMPANIES_FILE = os.path.join(PROJECT_ROOT, "companies.json")


def load_companies():
    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def html_to_text(raw_html):
    """Strip tags/entities from Greenhouse's HTML job content field.
    Greenhouse's 'content' field is entity-escaped (e.g. '&lt;div&gt;'), so tags
    only become real '<...>' after unescaping — unescape must run before stripping."""
    text = html.unescape(raw_html or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            print(f"  WARN: greenhouse/{slug} -> HTTP {resp.status_code}, skipping")
            return []
        jobs = resp.json().get("jobs", [])
        return [
            {
                "id": f"greenhouse:{slug}:{j['id']}",
                "title": j.get("title", ""),
                "company": slug,
                "location": (j.get("location") or {}).get("name", ""),
                "apply_url": j.get("absolute_url", ""),
                "source": "greenhouse",
                "description": "",
            }
            for j in jobs
        ]
    except requests.RequestException as e:
        print(f"  WARN: greenhouse/{slug} -> {e}, skipping")
        return []


def fetch_greenhouse_descriptions(slug, job_ids_wanted):
    """One request per company board, with content=true, to pull full descriptions
    — only called for boards that have shortlisted candidates, not all 4000+ postings."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            print(f"  WARN: greenhouse/{slug} content fetch -> HTTP {resp.status_code}, skipping")
            return {}
        jobs = resp.json().get("jobs", [])
        return {
            f"greenhouse:{slug}:{j['id']}": html_to_text(j.get("content", ""))
            for j in jobs
            if f"greenhouse:{slug}:{j['id']}" in job_ids_wanted
        }
    except requests.RequestException as e:
        print(f"  WARN: greenhouse/{slug} content fetch -> {e}, skipping")
        return {}


def _strip_json_ld_description(page_html):
    """Many ATS pages (Ashby, Workday, etc.) embed a schema.org JobPosting as
    <script type="application/ld+json">{"description": "<p>...</p>", ...}</script>.
    That embedded description is clean, structured job text — a much better source
    than stripping the whole rendered page. Try this first; return None if absent."""
    for block in re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page_html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("description"):
                return html_to_text(item["description"])
    return None


def fetch_generic_description(url):
    """Best-effort: fetch a job's own apply_url page and extract its text.
    Used as a fallback for sources with no bulk description (SimplifyJobs entries
    route to Ashby/Workday/company-custom sites with no shared API to batch-fetch
    from). Prefers a JobPosting JSON-LD block if the page has one (clean, targeted);
    otherwise falls back to the whole page stripped to text (messier — may include
    nav/footer content — but still far better than nothing)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return ""
        # requests falls back to a Latin-1 guess when the server doesn't declare a
        # charset, which garbles UTF-8 apostrophes/quotes into "â"-style mojibake.
        # apparent_encoding sniffs the actual bytes instead of trusting a missing header.
        resp.encoding = resp.apparent_encoding or resp.encoding
        json_ld_desc = _strip_json_ld_description(resp.text)
        if json_ld_desc:
            return json_ld_desc[:4000]
        # Strip <script>/<style> blocks (content included) before generic tag-stripping,
        # so embedded JS/JSON never gets treated as visible page text.
        cleaned = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", resp.text, flags=re.DOTALL | re.IGNORECASE)
        return html_to_text(cleaned)[:4000]
    except requests.RequestException:
        return ""


def enrich_descriptions(candidates):
    """Fill in full descriptions for a shortlist of jobs. Lever/RemoteOK/Adzuna
    already carry full text from the bulk fetch; Greenhouse gets one targeted
    request per company board (not per job). Anything still empty after that
    (mainly SimplifyJobs entries, which never carry a description) falls back to
    a best-effort direct fetch of the job's own apply_url, run concurrently since
    these are independent, slow, per-job HTTP requests."""
    greenhouse_ids_by_slug = {}
    for j in candidates:
        if j["source"] == "greenhouse":
            greenhouse_ids_by_slug.setdefault(j["company"], set()).add(j["id"])

    desc_map = {}
    for slug, ids_wanted in greenhouse_ids_by_slug.items():
        desc_map.update(fetch_greenhouse_descriptions(slug, ids_wanted))

    for j in candidates:
        if j["id"] in desc_map:
            j["description"] = desc_map[j["id"]]

    still_missing = [j for j in candidates if not j.get("description") and j.get("apply_url")]
    if still_missing:
        print(f"  Fetching fallback descriptions for {len(still_missing)} postings with no bulk description...")
        with ThreadPoolExecutor(max_workers=20) as pool:
            fetched = pool.map(lambda j: fetch_generic_description(j["apply_url"]), still_missing)
            for j, desc in zip(still_missing, fetched):
                j["description"] = desc

    return candidates


def fetch_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            print(f"  WARN: lever/{slug} -> HTTP {resp.status_code}, skipping")
            return []
        jobs = resp.json()
        return [
            {
                "id": f"lever:{slug}:{j['id']}",
                "title": j.get("text", ""),
                "company": slug,
                "location": (j.get("categories") or {}).get("location", ""),
                "apply_url": j.get("hostedUrl", ""),
                "source": "lever",
                "description": j.get("descriptionPlain", "") or "",
            }
            for j in jobs
        ]
    except requests.RequestException as e:
        print(f"  WARN: lever/{slug} -> {e}, skipping")
        return []


def fetch_remoteok(tags):
    url = "https://remoteok.com/api"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            print(f"  WARN: remoteok -> HTTP {resp.status_code}, skipping")
            return []
        entries = resp.json()
        out = []
        for j in entries:
            if "position" not in j:
                continue  # first entry is a legal notice, not a job
            job_tags = [t.lower() for t in j.get("tags", [])]
            if tags and not any(t in job_tags for t in tags):
                continue
            out.append({
                "id": f"remoteok:{j.get('id')}",
                "title": j.get("position", ""),
                "company": j.get("company", ""),
                "location": j.get("location", "Remote"),
                "apply_url": j.get("apply_url") or j.get("url", ""),
                "source": "remoteok",
                "description": html_to_text(j.get("description", ""))[:1500],
            })
        return out
    except requests.RequestException as e:
        print(f"  WARN: remoteok -> {e}, skipping")
        return []


def fetch_adzuna(config):
    app_id = config.get("adzuna_app_id", "")
    app_key = config.get("adzuna_app_key", "")
    if not app_id or "your-adzuna" in app_id:
        print("  SKIP: adzuna_app_id not configured (sign up free at adzuna.com/developer)")
        return []

    out = []
    for title in config.get("titles", []):
        url = "https://api.adzuna.com/v1/api/jobs/us/search/1"
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": 20,
            "what": title,
            "content-type": "application/json",
        }
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code != 200:
                print(f"  WARN: adzuna '{title}' -> HTTP {resp.status_code}, skipping")
                continue
            for j in resp.json().get("results", []):
                out.append({
                    "id": f"adzuna:{j.get('id')}",
                    "title": j.get("title", ""),
                    "company": (j.get("company") or {}).get("display_name", ""),
                    "location": (j.get("location") or {}).get("display_name", ""),
                    "apply_url": j.get("redirect_url", ""),
                    "source": "adzuna",
                    "description": html_to_text(j.get("description", ""))[:1500],
                })
        except requests.RequestException as e:
            print(f"  WARN: adzuna '{title}' -> {e}, skipping")
    return out


SIMPLIFY_FEEDS = {
    "simplify-newgrad": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "simplify-internships": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
}

SIMPLIFY_SPONSORSHIP_MAP = {
    "Offers Sponsorship": "yes",
    "Does Not Offer Sponsorship": "no",
    "U.S. Citizenship is Required": "no",
    "Other": "unknown",
}


def fetch_simplify(feed_key, feed_url):
    """SimplifyJobs' community-maintained new-grad/internship listings — updated
    continuously, free, published specifically for this purpose (not a scrape).
    Comes with a structured 'sponsorship' field (rare elsewhere) and a 'degrees'
    field, so we carry both through instead of relying only on description text."""
    try:
        resp = requests.get(feed_url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(f"  WARN: {feed_key} -> HTTP {resp.status_code}, skipping")
            return []
        entries = resp.json()
        out = []
        for j in entries:
            if not (j.get("active") and j.get("is_visible")):
                continue
            locations = j.get("locations") or ["Unknown"]
            out.append({
                "id": f"{feed_key}:{j.get('id')}",
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": "; ".join(locations),
                "apply_url": j.get("url", ""),
                "source": feed_key,
                "description": "",
                "sponsorship": SIMPLIFY_SPONSORSHIP_MAP.get(j.get("sponsorship"), "unknown"),
                "required_degrees": j.get("degrees", []) or [],
            })
        return out
    except requests.RequestException as e:
        print(f"  WARN: {feed_key} -> {e}, skipping")
        return []


def fetch_all_jobs(config):
    companies = load_companies()
    all_jobs = []

    # SimplifyJobs goes first: it's purpose-built for new-grad/intern fit (structured
    # sponsorship + degree fields), so if the downstream 300-candidate cap has to
    # truncate anything, it truncates the generic company boards, not this source.
    print("Fetching SimplifyJobs (new-grad + internships)...")
    for feed_key, feed_url in SIMPLIFY_FEEDS.items():
        jobs = fetch_simplify(feed_key, feed_url)
        print(f"  {feed_key}: {len(jobs)} active postings")
        all_jobs.extend(jobs)

    print("Fetching Greenhouse boards...")
    for slug in companies.get("greenhouse", []):
        jobs = fetch_greenhouse(slug)
        print(f"  greenhouse/{slug}: {len(jobs)} postings")
        all_jobs.extend(jobs)

    print("Fetching Lever boards...")
    for slug in companies.get("lever", []):
        jobs = fetch_lever(slug)
        print(f"  lever/{slug}: {len(jobs)} postings")
        all_jobs.extend(jobs)

    print("Fetching RemoteOK...")
    remoteok_tags = ["data-scientist", "data-analyst", "machine-learning", "ai", "python", "analyst"]
    jobs = fetch_remoteok(remoteok_tags)
    print(f"  remoteok: {len(jobs)} postings")
    all_jobs.extend(jobs)

    print("Fetching Adzuna...")
    jobs = fetch_adzuna(config)
    print(f"  adzuna: {len(jobs)} postings")
    all_jobs.extend(jobs)

    # dedupe by id
    seen_ids = set()
    deduped = []
    for j in all_jobs:
        if j["id"] not in seen_ids:
            seen_ids.add(j["id"])
            deduped.append(j)

    print(f"Total unique postings fetched: {len(deduped)}")
    return deduped


if __name__ == "__main__":
    # quick manual test — run with `python fetch_jobs.py`
    test_config = {"titles": ["Data Analyst"], "adzuna_app_id": "", "adzuna_app_key": ""}
    jobs = fetch_all_jobs(test_config)
    for j in jobs[:5]:
        print(j["title"], "|", j["company"], "|", j["location"], "|", j["apply_url"])
