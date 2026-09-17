"""
Skills Gap Analysis
Free, local, no API calls. Scans every job description accumulated in
data/applications.json (real postings this pipeline has already ranked highly
against your resume), counts how often each keyword in KEYWORDS appears, then
checks whether that keyword shows up anywhere in your resume or master
reference doc. Keywords that show up a lot in real postings but not in your
own material are the actual gaps worth learning.

Usage: python skills_gap.py
"""

import os
import re
from collections import Counter

from applications_store import load_applications

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
RESUME_TEX = os.path.join(PROJECT_ROOT, "resume", "resume_template.tex")
MASTER_REF = os.path.join(PROJECT_ROOT, "resume", "master_reference.md")

# Grouped for readability only — scored as one flat list. Add to this as your
# target roles shift; it's a plain list, no code changes needed elsewhere.
KEYWORDS = {
    "Cloud/Infra": ["AWS", "Azure", "GCP", "Docker", "Kubernetes", "Terraform", "Jenkins", "CI/CD"],
    "Data Engineering": ["Airflow", "Spark", "Kafka", "dbt", "Snowflake", "Redshift",
                          "BigQuery", "Hadoop", "ETL", "Databricks"],
    "ML/AI": ["PyTorch", "TensorFlow", "scikit-learn", "Keras", "Hugging Face", "MLflow",
              "Kubeflow", "LangChain", "RAG", "LLM", "NLP", "Computer Vision", "OpenCV",
              "XGBoost", "Reinforcement Learning"],
    "Data/Analytics": ["NoSQL", "MongoDB", "PostgreSQL", "MySQL", "Looker", "A/B testing"],
    "Languages": ["R", "Java", "Scala", "Go", "JavaScript", "TypeScript"],
    "Web/API": ["FastAPI", "Django", "REST API", "GraphQL", "gRPC", "Node.js"],
    "Practices": ["pytest", "unit testing", "Agile", "Scrum", "JIRA", "Git"],
}


def load_reference_text():
    text = ""
    for path in (RESUME_TEX, MASTER_REF):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                text += f.read().lower() + "\n"
    return text


def keyword_in_text(keyword, text):
    pattern = r"\b" + re.escape(keyword.lower()).replace(r"\ ", r"[\s-]?") + r"\b"
    return re.search(pattern, text) is not None


def run():
    apps = load_applications()
    descriptions = [a.get("description", "") for a in apps.values() if a.get("description")]
    if not descriptions:
        print("No job descriptions in data/applications.json yet - run main.py a few times first.")
        return

    reference_text = load_reference_text()
    all_keywords = [(kw, cat) for cat, kws in KEYWORDS.items() for kw in kws]

    counts = Counter()
    for kw, _ in all_keywords:
        pattern = re.compile(r"\b" + re.escape(kw.lower()).replace(r"\ ", r"[\s-]?") + r"\b")
        counts[kw] = sum(1 for d in descriptions if pattern.search(d.lower()))

    have = {kw for kw, _ in all_keywords if keyword_in_text(kw, reference_text)}
    gaps = [(kw, cat, counts[kw]) for kw, cat in all_keywords if kw not in have and counts[kw] > 0]
    gaps.sort(key=lambda x: x[2], reverse=True)

    n = len(descriptions)
    print(f"Scanned {n} real job descriptions accumulated so far.\n")
    print(f"{'Keyword':<20} {'Category':<18} {'Appears in':<12} {'On your resume?'}")
    print("-" * 65)
    for kw, cat, count in gaps:
        pct = count / n * 100
        print(f"{kw:<20} {cat:<18} {count}/{n} ({pct:.0f}%){'':<3} NO - gap")

    have_and_wanted = [(kw, counts[kw]) for kw, _ in all_keywords if kw in have and counts[kw] > 0]
    have_and_wanted.sort(key=lambda x: x[1], reverse=True)
    if have_and_wanted:
        print(f"\nAlready covered and actually asked for (good - keep these visible):")
        for kw, count in have_and_wanted[:8]:
            print(f"  {kw} - appears in {count}/{n} postings")

    if not gaps:
        print("No gaps found against this keyword list and this batch of postings.")
    else:
        print(f"\nTop learning priority: {gaps[0][0]} - shows up in {gaps[0][2]}/{n} postings "
              f"you're a real match for otherwise, and isn't on your resume at all.")


if __name__ == "__main__":
    run()
