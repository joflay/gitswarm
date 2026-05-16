from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests


def parse_github_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


@dataclass
class GitHubCommit:
    sha: str
    author_login: str
    message: str
    committed_at: datetime
    changed_files: int
    additions: int
    deletions: int
    files: list[dict[str, Any]]
    html_url: str


@dataclass
class GitHubPullRequest:
    number: int
    title: str
    author_login: str
    state: str
    opened_at: datetime | None
    merged_at: datetime | None
    comments_count: int
    reviews_count: int
    html_url: str


@dataclass
class GitHubIssue:
    number: int
    title: str
    author_login: str
    state: str
    opened_at: datetime | None
    closed_at: datetime | None
    comments_count: int
    html_url: str


class GitHubClient:
    def __init__(self, token: str, base_url: str = "https://api.github.com") -> None:
        if not token:
            raise ValueError("GITHUB_TOKEN is required for GitHub ingestion")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> list[Any]:
        url = f"{self.base_url}{path}"
        next_params = dict(params or {})
        next_params.setdefault("per_page", 100)
        items: list[Any] = []
        while url:
            response = self.session.get(url, params=next_params, timeout=30)
            response.raise_for_status()
            items.extend(response.json())
            url = response.links.get("next", {}).get("url")
            next_params = None
        return items

    def list_commits(self, owner: str, repo: str, since: datetime, until: datetime) -> list[GitHubCommit]:
        rows = self._paginate(
            f"/repos/{owner}/{repo}/commits",
            {"since": since.isoformat() + "Z", "until": until.isoformat() + "Z"},
        )
        commits: list[GitHubCommit] = []
        for row in rows:
            detail = self._get(f"/repos/{owner}/{repo}/commits/{row['sha']}")
            commit_data = detail.get("commit", {})
            stats = detail.get("stats", {})
            author = detail.get("author") or row.get("author") or {}
            files = detail.get("files", [])
            commits.append(
                GitHubCommit(
                    sha=detail["sha"],
                    author_login=author.get("login") or commit_data.get("author", {}).get("name", ""),
                    message=commit_data.get("message", ""),
                    committed_at=parse_github_datetime(commit_data.get("author", {}).get("date")) or since,
                    changed_files=len(files),
                    additions=int(stats.get("additions") or 0),
                    deletions=int(stats.get("deletions") or 0),
                    files=files,
                    html_url=detail.get("html_url", ""),
                )
            )
        return commits

    def list_pull_requests(self, owner: str, repo: str, since: datetime, until: datetime) -> list[GitHubPullRequest]:
        rows = self._paginate(f"/repos/{owner}/{repo}/pulls", {"state": "all", "sort": "updated", "direction": "desc"})
        pulls: list[GitHubPullRequest] = []
        for row in rows:
            opened_at = parse_github_datetime(row.get("created_at"))
            merged_at = parse_github_datetime(row.get("merged_at"))
            updated_at = parse_github_datetime(row.get("updated_at"))
            if not _in_window(opened_at, since, until) and not _in_window(merged_at, since, until) and not _in_window(updated_at, since, until):
                continue
            reviews = self._paginate(f"/repos/{owner}/{repo}/pulls/{row['number']}/reviews")
            comments = self._paginate(f"/repos/{owner}/{repo}/pulls/{row['number']}/comments")
            pulls.append(
                GitHubPullRequest(
                    number=int(row["number"]),
                    title=row.get("title", ""),
                    author_login=(row.get("user") or {}).get("login", ""),
                    state=row.get("state", ""),
                    opened_at=opened_at,
                    merged_at=merged_at,
                    comments_count=len([c for c in comments if _in_window(parse_github_datetime(c.get("created_at")), since, until)]),
                    reviews_count=len([r for r in reviews if _in_window(parse_github_datetime(r.get("submitted_at")), since, until)]),
                    html_url=row.get("html_url", ""),
                )
            )
        return pulls

    def list_issues(self, owner: str, repo: str, since: datetime, until: datetime) -> list[GitHubIssue]:
        rows = self._paginate(
            f"/repos/{owner}/{repo}/issues",
            {"state": "all", "since": since.isoformat() + "Z", "sort": "updated", "direction": "desc"},
        )
        issues: list[GitHubIssue] = []
        for row in rows:
            if "pull_request" in row:
                continue
            opened_at = parse_github_datetime(row.get("created_at"))
            closed_at = parse_github_datetime(row.get("closed_at"))
            updated_at = parse_github_datetime(row.get("updated_at"))
            if not _in_window(opened_at, since, until) and not _in_window(closed_at, since, until) and not _in_window(updated_at, since, until):
                continue
            comments = self._paginate(f"/repos/{owner}/{repo}/issues/{row['number']}/comments")
            issues.append(
                GitHubIssue(
                    number=int(row["number"]),
                    title=row.get("title", ""),
                    author_login=(row.get("user") or {}).get("login", ""),
                    state=row.get("state", ""),
                    opened_at=opened_at,
                    closed_at=closed_at,
                    comments_count=len([c for c in comments if _in_window(parse_github_datetime(c.get("created_at")), since, until)]),
                    html_url=row.get("html_url", ""),
                )
            )
        return issues


def _in_window(value: datetime | None, since: datetime, until: datetime) -> bool:
    return value is not None and since <= value <= until
