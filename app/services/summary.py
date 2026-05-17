from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import ActivitySummary, Checkpoint, Commit, Issue, OutsideWorkNote, ProgressReview, PullRequest, Repository, Team, User
from app.services.checkups import REPOSITORY_CHECKUP_NOTE_PREFIX
from app.services.scoring import score_summary


def rebuild_summaries(db: Session, checkpoint: Checkpoint) -> list[ActivitySummary]:
    db.query(ProgressReview).filter(ProgressReview.summary_id.in_(db.query(ActivitySummary.id).filter_by(checkpoint_id=checkpoint.id))).delete(
        synchronize_session=False
    )
    db.query(ActivitySummary).filter_by(checkpoint_id=checkpoint.id).delete()
    db.flush()

    summaries: list[ActivitySummary] = []
    summaries.extend(_member_summaries(db, checkpoint))
    summaries.extend(_repo_summaries(db, checkpoint))

    db.add_all(summaries)
    db.flush()
    for summary in summaries:
        score, status, review, explanation = score_summary(summary)
        db.add(ProgressReview(summary_id=summary.id, score=score, status=status, review=review, explanation=explanation))
    db.commit()
    return summaries


def _member_summaries(db: Session, checkpoint: Checkpoint) -> list[ActivitySummary]:
    repos = _checkpoint_repositories(db, checkpoint)
    repo_ids = [repo.id for repo in repos]
    users_query = db.query(User).filter_by(is_active=True, canonical_user_id=None)
    if len(repos) == 1 and _is_repository_checkup(checkpoint):
        team = db.query(Team).filter_by(name=repos[0].full_name).one_or_none()
        if team:
            users_query = users_query.filter_by(team_id=team.id)
    users = users_query.all()
    notes_by_user = _notes_by(db.query(OutsideWorkNote).filter_by(checkpoint_id=checkpoint.id).all(), "user_id")
    summaries = []
    for user in users:
        alias_ids = [alias.id for alias in user.aliases]
        user_ids = [user.id, *alias_ids]
        commit_query = db.query(Commit).filter(
            Commit.author_id.in_(user_ids),
            Commit.committed_at >= checkpoint.since,
            Commit.committed_at <= checkpoint.until,
        )
        if repo_ids:
            commit_query = commit_query.filter(Commit.repository_id.in_(repo_ids))
        commits = _dedupe_commits(
            commit_query.all()
        )
        prs = db.query(PullRequest).filter_by(checkpoint_id=checkpoint.id, author_login=user.github_username).all()
        issues = db.query(Issue).filter_by(checkpoint_id=checkpoint.id, author_login=user.github_username).all()
        notes = notes_by_user[user.id]
        for alias_id in alias_ids:
            notes.extend(notes_by_user[alias_id])
        summaries.append(_build_summary(checkpoint, commits, prs, issues, "member", user.github_username, user_id=user.id, team_id=user.team_id, notes=notes))
    return summaries


def _repo_summaries(db: Session, checkpoint: Checkpoint) -> list[ActivitySummary]:
    repos = _checkpoint_repositories(db, checkpoint)
    notes_by_repo = _notes_by(db.query(OutsideWorkNote).filter_by(checkpoint_id=checkpoint.id).all(), "repository_id")
    summaries = []
    for repo in repos:
        commits = _dedupe_commits(
            db.query(Commit)
            .filter(Commit.repository_id == repo.id, Commit.committed_at >= checkpoint.since, Commit.committed_at <= checkpoint.until)
            .all()
        )
        prs = db.query(PullRequest).filter_by(checkpoint_id=checkpoint.id, repository_id=repo.id).all()
        issues = db.query(Issue).filter_by(checkpoint_id=checkpoint.id, repository_id=repo.id).all()
        summaries.append(_build_summary(checkpoint, commits, prs, issues, "repo", repo.full_name, repository_id=repo.id, notes=notes_by_repo[repo.id]))
    return summaries


def _checkpoint_repositories(db: Session, checkpoint: Checkpoint) -> list[Repository]:
    if _is_repository_checkup(checkpoint):
        full_name = checkpoint.notes.removeprefix(REPOSITORY_CHECKUP_NOTE_PREFIX)
        repo = db.query(Repository).filter_by(full_name=full_name).one_or_none()
        return [repo] if repo else []
    return db.query(Repository).filter_by(is_active=True, org_name=checkpoint.org_name).all()


def _is_repository_checkup(checkpoint: Checkpoint) -> bool:
    return checkpoint.notes.startswith(REPOSITORY_CHECKUP_NOTE_PREFIX)


def _build_summary(
    checkpoint: Checkpoint,
    commits: list[Commit],
    prs: list[PullRequest],
    issues: list[Issue],
    scope: str,
    subject: str,
    repository_id: int | None = None,
    user_id: int | None = None,
    team_id: int | None = None,
    notes: list[str] | None = None,
) -> ActivitySummary:
    notes = notes or []
    active_days = {commit.committed_at.date().isoformat() for commit in commits}
    files: list[str] = []
    messages: list[str] = []
    for commit in commits:
        messages.append(commit.message)
        for file_row in json.loads(commit.files_json or "[]"):
            if isinstance(file_row, dict) and file_row.get("filename"):
                files.append(file_row["filename"])
    return ActivitySummary(
        checkpoint_id=checkpoint.id,
        repository_id=repository_id,
        user_id=user_id,
        team_id=team_id,
        scope=scope,
        subject=subject,
        commits_count=len(commits),
        changed_files=sum(commit.changed_files for commit in commits),
        additions=sum(commit.additions for commit in commits),
        deletions=sum(commit.deletions for commit in commits),
        prs_opened=sum(1 for pr in prs if pr.opened_at and checkpoint.since <= pr.opened_at <= checkpoint.until),
        prs_merged=sum(1 for pr in prs if pr.merged_at and checkpoint.since <= pr.merged_at <= checkpoint.until),
        issues_opened=sum(1 for issue in issues if issue.opened_at and checkpoint.since <= issue.opened_at <= checkpoint.until),
        issues_closed=sum(1 for issue in issues if issue.closed_at and checkpoint.since <= issue.closed_at <= checkpoint.until),
        comments_reviews=sum(pr.comments_count + pr.reviews_count for pr in prs) + sum(issue.comments_count for issue in issues),
        active_days=len(active_days),
        details_json=json.dumps({"files": files, "commit_messages": messages, "outside_work_notes": notes}),
    )


def _notes_by(notes: list[OutsideWorkNote], field: str) -> defaultdict[int, list[str]]:
    grouped: defaultdict[int, list[str]] = defaultdict(list)
    for note in notes:
        key = getattr(note, field)
        if key is not None:
            grouped[key].append(note.note)
    return grouped


def _dedupe_commits(commits: list[Commit]) -> list[Commit]:
    by_repo_sha: dict[tuple[int, str], Commit] = {}
    for commit in commits:
        by_repo_sha[(commit.repository_id, commit.sha)] = commit
    return list(by_repo_sha.values())
