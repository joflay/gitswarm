from __future__ import annotations

import json
import re

from app.models import ActivitySummary


ISSUE_REF_RE = re.compile(r"(#\d+|[A-Z][A-Z0-9]+-\d+|fix(es)?|close(s|d)?)", re.IGNORECASE)


def message_quality_score(messages: list[str]) -> tuple[int, str]:
    if not messages:
        return 0, "No commit messages were available."
    quality_hits = 0
    for message in messages:
        first_line = message.strip().splitlines()[0] if message.strip() else ""
        if len(first_line) >= 12 and not first_line.lower().startswith(("wip", "update", "changes", "misc")):
            quality_hits += 1
        if ISSUE_REF_RE.search(message):
            quality_hits += 1
    score = min(20, round((quality_hits / max(1, len(messages) * 2)) * 20))
    return score, f"Commit messages earned {score}/20 for specificity and issue references."


def file_relevance_score(files: list[str]) -> tuple[int, str]:
    if not files:
        return 0, "No changed files were recorded."
    score = 0
    source_hits = 0
    test_hits = 0
    docs_hits = 0
    for filename in files:
        lowered = filename.lower()
        if lowered.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".sql")):
            score += 3
            source_hits += 1
        elif "test" in lowered or "spec" in lowered:
            score += 2
            test_hits += 1
        elif lowered.endswith((".md", ".rst")) or "docs/" in lowered:
            score += 1
            docs_hits += 1
        else:
            score += 1
    capped = min(20, score)
    return capped, f"File relevance earned {capped}/20 from {source_hits} source, {test_hits} test, and {docs_hits} docs files."


def score_summary(summary: ActivitySummary) -> tuple[int, str, str, str]:
    details = json.loads(summary.details_json or "{}")
    messages = details.get("commit_messages", [])
    files = details.get("files", [])
    message_score, message_reason = message_quality_score(messages)
    relevance_score, relevance_reason = file_relevance_score(files)

    volume = min(25, summary.additions // 40 + summary.deletions // 80 + summary.changed_files * 2 + summary.commits_count * 3)
    collaboration = min(
        25,
        summary.prs_opened * 5
        + summary.prs_merged * 8
        + summary.issues_opened * 3
        + summary.issues_closed * 5
        + summary.comments_reviews * 2,
    )
    consistency = min(10, summary.active_days * 3)
    note_bonus = min(5, len(details.get("outside_work_notes", [])) * 2)
    score = min(100, volume + collaboration + consistency + message_score + relevance_score + note_bonus)

    if score >= 60:
        status = "green"
        review = "sufficient progress"
    elif score >= 35:
        status = "yellow"
        review = "needs human review"
    else:
        status = "red"
        review = "weak progress"

    explanation = (
        f"Score {score}/100. Volume contributed {volume}/25 from {summary.commits_count} commits, "
        f"{summary.changed_files} files, +{summary.additions}/-{summary.deletions}. "
        f"Collaboration contributed {collaboration}/25 from {summary.prs_opened} opened PRs, "
        f"{summary.prs_merged} merged PRs, {summary.issues_opened} opened issues, "
        f"{summary.issues_closed} closed issues, and {summary.comments_reviews} comments/reviews. "
        f"Consistency contributed {consistency}/10 across {summary.active_days} active days. "
        f"{message_reason} {relevance_reason}"
    )
    if note_bonus:
        explanation += f" Outside work notes added contextual support worth {note_bonus}/5."
    return score, status, review, explanation
