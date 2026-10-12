from datetime import datetime

from app.main import _checkpoint_form_context, _dashboard_context, _latest_member_scores, _repository_health_context
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


def test_checkpoint_form_context_prefills_next_interval(sqlite_session):
    sqlite_session.add_all(
        [
            Repository(org_name="acme", name="web", full_name="acme/web"),
            Repository(org_name="beta", name="api", full_name="beta/api"),
        ]
    )
    checkpoint = Checkpoint(
        org_name="acme",
        since=datetime(2026, 5, 1, 9),
        until=datetime(2026, 5, 8, 9),
        checkpoint_day_time="Friday 09:00",
    )
    sqlite_session.add(checkpoint)
    sqlite_session.commit()

    form = _checkpoint_form_context(sqlite_session, checkpoint)

    assert form["org_options"] == ["acme", "beta"]
    assert form["selected_org"] == "acme"
    assert form["since"] == "2026-05-08T09:00"
    assert form["until"] == "2026-05-15T09:00"
    assert form["checkpoint_day_time"] == "Friday 09:00"


def test_latest_member_scores_uses_latest_org_checkpoint(sqlite_session):
    user = User(github_username="alice")
    older = Checkpoint(org_name="acme", since=datetime(2026, 5, 1), until=datetime(2026, 5, 8))
    latest = Checkpoint(org_name="acme", since=datetime(2026, 5, 8), until=datetime(2026, 5, 15))
    repo_checkup = Checkpoint(
        org_name="acme",
        since=datetime(2026, 5, 15),
        until=datetime(2026, 5, 22),
        notes=f"{REPOSITORY_CHECKUP_NOTE_PREFIX}acme/web",
    )
    sqlite_session.add_all([user, older, latest, repo_checkup])
    sqlite_session.flush()

    older_summary = ActivitySummary(checkpoint_id=older.id, user_id=user.id, scope="member", subject="alice")
    latest_summary = ActivitySummary(checkpoint_id=latest.id, user_id=user.id, scope="member", subject="alice")
    checkup_summary = ActivitySummary(checkpoint_id=repo_checkup.id, user_id=user.id, scope="member", subject="alice")
    sqlite_session.add_all([older_summary, latest_summary, checkup_summary])
    sqlite_session.flush()
    sqlite_session.add_all(
        [
            ProgressReview(summary_id=older_summary.id, status="yellow", review="limited progress", score=40),
            ProgressReview(summary_id=latest_summary.id, status="green", review="sufficient progress", score=82),
            ProgressReview(summary_id=checkup_summary.id, status="red", review="weak progress", score=10),
        ]
    )
    sqlite_session.commit()

    assert _latest_member_scores(sqlite_session, [user]) == {user.id: 82}
