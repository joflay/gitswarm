from __future__ import annotations

from pathlib import Path

import markdown as markdown_lib
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.models import ActivitySummary, Checkpoint, OutsideWorkNote


def render_markdown_report(db: Session, checkpoint: Checkpoint) -> str:
    summaries = (
        db.query(ActivitySummary)
        .options(joinedload(ActivitySummary.review))
        .filter_by(checkpoint_id=checkpoint.id)
        .order_by(ActivitySummary.scope, ActivitySummary.subject)
        .all()
    )
    notes = db.query(OutsideWorkNote).filter_by(checkpoint_id=checkpoint.id).all()
    lines = [
        f"# GitSwarm Weekly Report: {checkpoint.org_name}",
        "",
        f"**Window:** {checkpoint.since.isoformat()} to {checkpoint.until.isoformat()}",
        "",
        "## Progress Summary",
        "",
        "| Scope | Subject | Status | Review | Score | Evidence |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for summary in summaries:
        review = summary.review
        lines.append(
            "| "
            + " | ".join(
                [
                    summary.scope,
                    summary.subject,
                    review.status if review else "",
                    review.review if review else "",
                    str(review.score if review else 0),
                    _escape_table(review.explanation if review else ""),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Outside GitHub Work", ""])
    if notes:
        for note in notes:
            lines.append(f"- {note.note}")
    else:
        lines.append("_No outside work notes recorded._")
    lines.extend(["", "## Low Activity Flags", ""])
    low = [summary for summary in summaries if summary.scope == "member" and summary.review and summary.review.status == "red"]
    if low:
        for summary in low:
            lines.append(f"- **{summary.subject}**: {summary.review.explanation}")
    else:
        lines.append("_No red member flags._")
    return "\n".join(lines) + "\n"


def export_report(db: Session, checkpoint: Checkpoint, fmt: str = "markdown") -> Path:
    settings = get_settings()
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    markdown_text = render_markdown_report(db, checkpoint)
    stem = f"gitswarm-checkpoint-{checkpoint.id}"
    if fmt == "markdown":
        path = settings.report_dir / f"{stem}.md"
        path.write_text(markdown_text, encoding="utf-8")
        return path
    if fmt == "pdf":
        path = settings.report_dir / f"{stem}.pdf"
        html = markdown_lib.markdown(markdown_text, extensions=["tables"])
        try:
            from weasyprint import HTML

            HTML(string=_html_document(html)).write_pdf(path)
        except Exception:
            path.write_text(markdown_text, encoding="utf-8")
        return path
    raise ValueError("format must be markdown or pdf")


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _html_document(body: str) -> str:
    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: Arial, sans-serif; color: #1d2433; line-height: 1.45; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
    th, td {{ border: 1px solid #d6dbe6; padding: 6px; vertical-align: top; }}
    th {{ background: #eef2f7; }}
  </style>
</head>
<body>{body}</body>
</html>
"""
