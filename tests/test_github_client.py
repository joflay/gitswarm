from datetime import datetime

import requests_mock

from app.github_client import GitHubClient


def test_list_commits_normalizes_commit_detail():
    with requests_mock.Mocker() as mocker:
        mocker.get("https://api.github.com/repos/acme/web/commits", json=[{"sha": "abc", "author": {"login": "alice"}}])
        mocker.get(
            "https://api.github.com/repos/acme/web/commits/abc",
            json={
                "sha": "abc",
                "author": {"login": "alice"},
                "commit": {"message": "Add weekly report #1", "author": {"date": "2026-05-01T10:00:00Z"}},
                "stats": {"additions": 20, "deletions": 3},
                "files": [{"filename": "app/main.py"}],
                "html_url": "https://github.com/acme/web/commit/abc",
            },
        )
        client = GitHubClient("token")
        commits = client.list_commits("acme", "web", datetime(2026, 5, 1), datetime(2026, 5, 8))

    assert len(commits) == 1
    assert commits[0].author_login == "alice"
    assert commits[0].additions == 20
    assert commits[0].files[0]["filename"] == "app/main.py"


def test_list_collaborators_returns_sorted_unique_logins():
    with requests_mock.Mocker() as mocker:
        mocker.get(
            "https://api.github.com/repos/acme/web/collaborators",
            json=[{"login": "zoe"}, {"login": "alice"}, {"login": "alice"}, {"name": "missing-login"}],
        )
        client = GitHubClient("token")
        collaborators = client.list_collaborators("acme", "web")

    assert collaborators == ["alice", "zoe"]
