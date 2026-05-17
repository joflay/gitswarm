from datetime import datetime

from app.main import _delete_team
from app.models import ActivitySummary, Checkpoint, OutsideWorkNote, Team, User


def test_delete_team_unassigns_related_records(sqlite_session):
    team = Team(name="acme/web")
    checkpoint = Checkpoint(org_name="acme", since=datetime(2026, 5, 1), until=datetime(2026, 5, 8))
    sqlite_session.add_all([team, checkpoint])
    sqlite_session.flush()
    user = User(github_username="alice", team_id=team.id)
    summary = ActivitySummary(checkpoint_id=checkpoint.id, team_id=team.id, scope="member", subject="alice")
    note = OutsideWorkNote(checkpoint_id=checkpoint.id, team_id=team.id, note="modeled factor risk")
    sqlite_session.add_all([user, summary, note])
    sqlite_session.commit()

    _delete_team(sqlite_session, team)

    assert sqlite_session.get(Team, team.id) is None
    assert sqlite_session.get(User, user.id).team_id is None
    assert sqlite_session.get(ActivitySummary, summary.id).team_id is None
    assert sqlite_session.get(OutsideWorkNote, note.id).team_id is None
