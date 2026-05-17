import json
from datetime import datetime

from app.models import ActivitySummary, Checkpoint, Commit, Repository, Team, User
from app.services.scoring import score_summary
from app.services.summary import rebuild_summaries


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


def test_rebuild_summaries_omits_team_scope(sqlite_session):
    team = Team(name="platform")
    sqlite_session.add(team)
    sqlite_session.flush()
    sqlite_session.add(User(github_username="alice", team_id=team.id))
    sqlite_session.add(Repository(org_name="acme", name="platform", full_name="acme/platform"))
    checkpoint = Checkpoint(org_name="acme", since=datetime(2026, 5, 1), until=datetime(2026, 5, 8))
    sqlite_session.add(checkpoint)
    sqlite_session.commit()

    summaries = rebuild_summaries(sqlite_session, checkpoint)

    assert {summary.scope for summary in summaries} == {"member", "repo"}


def test_rebuild_summaries_rolls_alias_commits_into_canonical_member(sqlite_session):
    team = Team(name="acme/web")
    canonical = User(github_username="joflay", team=team)
    alias = User(github_username="Jorge", team=team, canonical_user=canonical)
    repo = Repository(org_name="acme", name="web", full_name="acme/web")
    checkpoint = Checkpoint(org_name="acme", since=datetime(2026, 5, 1), until=datetime(2026, 5, 8))
    sqlite_session.add_all([team, canonical, alias, repo, checkpoint])
    sqlite_session.flush()
    sqlite_session.add_all(
        [
            Commit(
                checkpoint_id=checkpoint.id,
                repository_id=repo.id,
                author_id=canonical.id,
                author_login="joflay",
                sha="abc",
                message="canonical work",
                committed_at=datetime(2026, 5, 2),
            ),
            Commit(
                checkpoint_id=checkpoint.id,
                repository_id=repo.id,
                author_id=alias.id,
                author_login="Jorge",
                sha="def",
                message="alias work",
                committed_at=datetime(2026, 5, 3),
            ),
        ]
    )
    sqlite_session.commit()

    summaries = rebuild_summaries(sqlite_session, checkpoint)

    member_summaries = [summary for summary in summaries if summary.scope == "member"]
    assert len(member_summaries) == 1
    assert member_summaries[0].subject == "joflay"
    assert member_summaries[0].commits_count == 2
