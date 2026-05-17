from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import get_settings
from app.github_client import GitHubClient
from app.models import Checkpoint, Commit, Issue, PullRequest, Repository, User
from app.services.summary import rebuild_summaries


HISTORY_CHECKPOINT_DAY_TIME = "commit-history-cache"
HISTORY_SINCE = datetime(1970, 1, 1)


def ingest_activity(db: Session, org: str | None, since: datetime, until: datetime, checkpoint_day_time: str = "") -> Checkpoint:
    repositories = db.query(Repository).filter_by(is_active=True).all()
    if org:
        repositories = [repo for repo in repositories if repo.org_name == org]
    elif repositories:
        org = repositories[0].org_name
    if not repositories:
        raise ValueError("No active repositories configured for ingestion")

    checkpoint = Checkpoint(org_name=org or repositories[0].org_name, since=since, until=until, checkpoint_day_time=checkpoint_day_time)
    db.add(checkpoint)
    db.commit()
    rebuild_summaries(db, checkpoint)
    db.refresh(checkpoint)
    return checkpoint


def refresh_checkpoint_activity(db: Session, checkpoint: Checkpoint) -> Checkpoint:
    repositories = db.query(Repository).filter_by(is_active=True, org_name=checkpoint.org_name).all()
    if not repositories:
        raise ValueError(f"No active repositories configured for {checkpoint.org_name}")

    rebuild_summaries(db, checkpoint)
    db.refresh(checkpoint)
    return checkpoint


def refresh_repository_commit_history(db: Session, repo: Repository) -> int:
    client = GitHubClient(get_settings().github_token)
    checkpoint = _history_checkpoint(db, repo)
    db.query(Commit).filter_by(checkpoint_id=checkpoint.id, repository_id=repo.id).delete(synchronize_session=False)
    db.flush()

    users_by_login = {user.github_username.lower(): user for user in db.query(User).all()}
    commits = client.list_commits(repo.org_name, repo.name)
    for item in commits:
        _add_commit(db, checkpoint, repo, users_by_login, item)
    checkpoint.until = datetime.utcnow()
    db.commit()
    return len(commits)


def _history_checkpoint(db: Session, repo: Repository) -> Checkpoint:
    notes = f"commit-history:{repo.full_name}"
    checkpoint = db.query(Checkpoint).filter_by(notes=notes, checkpoint_day_time=HISTORY_CHECKPOINT_DAY_TIME).one_or_none()
    if checkpoint:
        checkpoint.org_name = repo.org_name
        checkpoint.since = HISTORY_SINCE
        checkpoint.until = datetime.utcnow()
        return checkpoint

    checkpoint = Checkpoint(
        org_name=repo.org_name,
        since=HISTORY_SINCE,
        until=datetime.utcnow(),
        checkpoint_day_time=HISTORY_CHECKPOINT_DAY_TIME,
        notes=notes,
    )
    db.add(checkpoint)
    db.flush()
    return checkpoint


def _ingest_repo(db: Session, client: GitHubClient, checkpoint: Checkpoint, repo: Repository, users_by_login: dict[str, User]) -> None:
    commits = client.list_commits(repo.org_name, repo.name, checkpoint.since, checkpoint.until)
    for item in commits:
        _add_commit(db, checkpoint, repo, users_by_login, item)

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


def _add_commit(db: Session, checkpoint: Checkpoint, repo: Repository, users_by_login: dict[str, User], item) -> None:
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
