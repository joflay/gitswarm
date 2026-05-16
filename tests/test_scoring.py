import json
from datetime import datetime

from app.models import ActivitySummary
from app.services.scoring import score_summary


def make_summary(**kwargs):
    defaults = dict(
        checkpoint_id=1,
        scope="member",
        subject="alice",
        commits_count=0,
        changed_files=0,
        additions=0,
        deletions=0,
        prs_opened=0,
        prs_merged=0,
        issues_opened=0,
        issues_closed=0,
        comments_reviews=0,
        active_days=0,
        details_json=json.dumps({"files": [], "commit_messages": [], "outside_work_notes": []}),
    )
    defaults.update(kwargs)
    return ActivitySummary(**defaults)


def test_high_activity_scores_sufficient_progress():
    summary = make_summary(
        commits_count=5,
        changed_files=8,
        additions=500,
        deletions=120,
        prs_opened=2,
        prs_merged=1,
        issues_closed=2,
        comments_reviews=4,
        active_days=3,
        details_json=json.dumps(
            {
                "files": ["app/main.py", "app/service.py", "tests/test_service.py"],
                "commit_messages": ["Add checkpoint ingestion fixes #12", "Close API pagination bug"],
                "outside_work_notes": [],
            }
        ),
    )
    score, status, review, explanation = score_summary(summary)
    assert score >= 60
    assert status == "green"
    assert review == "sufficient progress"
    assert "Volume contributed" in explanation


def test_low_activity_scores_weak_progress():
    score, status, review, _ = score_summary(make_summary())
    assert score < 35
    assert status == "red"
    assert review == "weak progress"
