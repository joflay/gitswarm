from datetime import datetime

from app.models import Checkpoint
from app.services.reports import render_markdown_report


def test_render_markdown_report_empty_db(sqlite_session):
    checkpoint = Checkpoint(org_name="acme", since=datetime(2026, 5, 1), until=datetime(2026, 5, 8))
    sqlite_session.add(checkpoint)
    sqlite_session.commit()

    report = render_markdown_report(sqlite_session, checkpoint)

    assert "# GitSwarm Weekly Report: acme" in report
    assert "No outside work notes recorded" in report
