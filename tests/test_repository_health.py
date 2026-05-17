from datetime import datetime

from app.main import _dashboard_context, _repository_health_context
from app.models import ActivitySummary, Checkpoint, ProgressReview, Repository, Team, User
from app.services.checkups import REPOSITORY_CHECKUP_NOTE_PREFIX


def test_repository_health_uses_latest_repository_checkup(sqlite_session):
    repo = Repository(org_name="acme", name="web", full_name="acme/web")
    team = Team(name="acme/web")
    user = User(github_username="alice", team=team)
    older = Checkpoint(
        org_name="acme",
        since=datetime(2026, 5, 1),
        until=datetime(2026, 5, 8),
        notes=f"{REPOSITORY_CHECKUP_NOTE_PREFIX}acme/web",
    )
    latest = Checkpoint(
        org_name="acme",
        since=datetime(2026, 5, 8),
        until=datetime(2026, 5, 15),
        notes=f"{REPOSITORY_CHECKUP_NOTE_PREFIX}acme/web",
    )
    sqlite_session.add_all([repo, team, user, older, latest])
    sqlite_session.flush()
    repo_summary = ActivitySummary(checkpoint_id=latest.id, repository_id=repo.id, scope="repo", subject="acme/web")
    member_summary = ActivitySummary(checkpoint_id=latest.id, user_id=user.id, scope="member", subject="alice")
    sqlite_session.add_all([repo_summary, member_summary])
    sqlite_session.flush()
    sqlite_session.add(ProgressReview(summary_id=repo_summary.id, status="green", review="sufficient progress", score=84))
    sqlite_session.commit()

    health = _repository_health_context(sqlite_session, repo)

    assert health["checkpoint"].id == latest.id
    assert health["repo_summary"].review.score == 84
    assert [summary.subject for summary in health["member_summaries"]] == ["alice"]


def test_dashboard_context_excludes_repository_checkups(sqlite_session):
    repo_checkup = Checkpoint(
        org_name="acme",
        since=datetime(2026, 5, 8),
        until=datetime(2026, 5, 15),
        notes=f"{REPOSITORY_CHECKUP_NOTE_PREFIX}acme/web",
    )
    org_checkpoint = Checkpoint(org_name="acme", since=datetime(2026, 5, 1), until=datetime(2026, 5, 8))
    sqlite_session.add_all([repo_checkup, org_checkpoint])
    sqlite_session.commit()

    context = _dashboard_context(object(), sqlite_session)

    assert context["checkpoint"].id == org_checkpoint.id
