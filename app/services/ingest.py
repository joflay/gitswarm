from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import get_settings
from app.github_client import GitHubClient
from app.models import Checkpoint, Commit, Issue, PullRequest, Repository, User
from app.services.summary import rebuild_summaries


def ingest_activity(db: Session, org: str | None, since: datetime, until: datetime) -> Checkpoint:
    settings = get_settings()
    client = GitHubClient(settings.github_token)
    repositories = db.query(Repository).filter_by(is_active=True).all()
    if org:
        repositories = [repo for repo in repositories if repo.org_name == org]
    elif repositories:
        org = repositories[0].org_name
    if not repositories:
        raise ValueError("No active repositories configured for ingestion")

    checkpoint = Checkpoint(org_name=org or repositories[0].org_name, since=since, until=until)
    db.add(checkpoint)
    db.flush()

    users_by_login = {user.github_username.lower(): user for user in db.query(User).all()}
    for repo in repositories:
        _ingest_repo(db, client, checkpoint, repo, users_by_login)

    db.commit()
    rebuild_summaries(db, checkpoint)
    db.refresh(checkpoint)
    return checkpoint


def _ingest_repo(db: Session, client: GitHubClient, checkpoint: Checkpoint, repo: Repository, users_by_login: dict[str, User]) -> None:
    commits = client.list_commits(repo.org_name, repo.name, checkpoint.since, checkpoint.until)
    for item in commits:
        user = users_by_login.get(item.author_login.lower())
        db.add(
            Commit(
                checkpoint_id=checkpoint.id,
                repository_id=repo.id,
                author_id=user.id if user else None,
                author_login=item.author_login,
                sha=item.sha,
                message=item.message,
                committed_at=item.committed_at,
                changed_files=item.changed_files,
                additions=item.additions,
                deletions=item.deletions,
                files_json=json.dumps(item.files),
                html_url=item.html_url,
            )
        )

    pulls = client.list_pull_requests(repo.org_name, repo.name, checkpoint.since, checkpoint.until)
    for item in pulls:
        db.add(
            PullRequest(
                checkpoint_id=checkpoint.id,
                repository_id=repo.id,
                author_login=item.author_login,
                number=item.number,
                title=item.title,
                state=item.state,
                opened_at=item.opened_at,
                merged_at=item.merged_at,
                comments_count=item.comments_count,
                reviews_count=item.reviews_count,
                html_url=item.html_url,
            )
        )

    issues = client.list_issues(repo.org_name, repo.name, checkpoint.since, checkpoint.until)
    for item in issues:
        db.add(
            Issue(
                checkpoint_id=checkpoint.id,
                repository_id=repo.id,
                author_login=item.author_login,
                number=item.number,
                title=item.title,
                state=item.state,
                opened_at=item.opened_at,
                closed_at=item.closed_at,
                comments_count=item.comments_count,
                html_url=item.html_url,
            )
        )
