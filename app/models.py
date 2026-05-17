from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    github_username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    canonical_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    team: Mapped[Team | None] = relationship(back_populates="members")
    canonical_user: Mapped[User | None] = relationship(remote_side="User.id", back_populates="aliases")
    aliases: Mapped[list[User]] = relationship(back_populates="canonical_user")
    commits: Mapped[list[Commit]] = relationship(back_populates="author")
    summaries: Mapped[list[ActivitySummary]] = relationship(back_populates="user")


class Team(TimestampMixin, Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)

    members: Mapped[list[User]] = relationship(back_populates="team")
    summaries: Mapped[list[ActivitySummary]] = relationship(back_populates="team")


class Repository(TimestampMixin, Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("org_name", "name", name="uq_repository_org_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_name: Mapped[str] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    full_name: Mapped[str] = mapped_column(String(420), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    relevance_rules: Mapped[str] = mapped_column(Text, default="")

    commits: Mapped[list[Commit]] = relationship(back_populates="repository")
    pull_requests: Mapped[list[PullRequest]] = relationship(back_populates="repository")
    issues: Mapped[list[Issue]] = relationship(back_populates="repository")
    summaries: Mapped[list[ActivitySummary]] = relationship(back_populates="repository")


class Checkpoint(TimestampMixin, Base):
    __tablename__ = "checkpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_name: Mapped[str] = mapped_column(String(200), index=True)
    since: Mapped[datetime] = mapped_column(DateTime, index=True)
    until: Mapped[datetime] = mapped_column(DateTime, index=True)
    checkpoint_day_time: Mapped[str] = mapped_column(String(120), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    commits: Mapped[list[Commit]] = relationship(back_populates="checkpoint", cascade="all, delete-orphan")
    pull_requests: Mapped[list[PullRequest]] = relationship(back_populates="checkpoint", cascade="all, delete-orphan")
    issues: Mapped[list[Issue]] = relationship(back_populates="checkpoint", cascade="all, delete-orphan")
    summaries: Mapped[list[ActivitySummary]] = relationship(back_populates="checkpoint", cascade="all, delete-orphan")
    outside_work_notes: Mapped[list[OutsideWorkNote]] = relationship(back_populates="checkpoint", cascade="all, delete-orphan")


class Commit(TimestampMixin, Base):
    __tablename__ = "commits"
    __table_args__ = (UniqueConstraint("repository_id", "sha", "checkpoint_id", name="uq_commit_repo_sha_checkpoint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    checkpoint_id: Mapped[int] = mapped_column(ForeignKey("checkpoints.id"), index=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    author_login: Mapped[str] = mapped_column(String(120), index=True, default="")
    sha: Mapped[str] = mapped_column(String(80), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    committed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    changed_files: Mapped[int] = mapped_column(Integer, default=0)
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    files_json: Mapped[str] = mapped_column(Text, default="[]")
    html_url: Mapped[str] = mapped_column(Text, default="")

    checkpoint: Mapped[Checkpoint] = relationship(back_populates="commits")
    repository: Mapped[Repository] = relationship(back_populates="commits")
    author: Mapped[User | None] = relationship(back_populates="commits")


class PullRequest(TimestampMixin, Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repository_id", "number", "checkpoint_id", name="uq_pr_repo_number_checkpoint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    checkpoint_id: Mapped[int] = mapped_column(ForeignKey("checkpoints.id"), index=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    author_login: Mapped[str] = mapped_column(String(120), index=True, default="")
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(40), default="")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    comments_count: Mapped[int] = mapped_column(Integer, default=0)
    reviews_count: Mapped[int] = mapped_column(Integer, default=0)
    html_url: Mapped[str] = mapped_column(Text, default="")

    checkpoint: Mapped[Checkpoint] = relationship(back_populates="pull_requests")
    repository: Mapped[Repository] = relationship(back_populates="pull_requests")


class Issue(TimestampMixin, Base):
    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("repository_id", "number", "checkpoint_id", name="uq_issue_repo_number_checkpoint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    checkpoint_id: Mapped[int] = mapped_column(ForeignKey("checkpoints.id"), index=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    author_login: Mapped[str] = mapped_column(String(120), index=True, default="")
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(40), default="")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    comments_count: Mapped[int] = mapped_column(Integer, default=0)
    html_url: Mapped[str] = mapped_column(Text, default="")

    checkpoint: Mapped[Checkpoint] = relationship(back_populates="issues")
    repository: Mapped[Repository] = relationship(back_populates="issues")


class ActivitySummary(TimestampMixin, Base):
    __tablename__ = "activity_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    checkpoint_id: Mapped[int] = mapped_column(ForeignKey("checkpoints.id"), index=True)
    repository_id: Mapped[int | None] = mapped_column(ForeignKey("repositories.id"), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(40), index=True)
    subject: Mapped[str] = mapped_column(String(240), index=True)
    commits_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_files: Mapped[int] = mapped_column(Integer, default=0)
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    prs_opened: Mapped[int] = mapped_column(Integer, default=0)
    prs_merged: Mapped[int] = mapped_column(Integer, default=0)
    issues_opened: Mapped[int] = mapped_column(Integer, default=0)
    issues_closed: Mapped[int] = mapped_column(Integer, default=0)
    comments_reviews: Mapped[int] = mapped_column(Integer, default=0)
    active_days: Mapped[int] = mapped_column(Integer, default=0)
    details_json: Mapped[str] = mapped_column(Text, default="{}")

    checkpoint: Mapped[Checkpoint] = relationship(back_populates="summaries")
    repository: Mapped[Repository | None] = relationship(back_populates="summaries")
    user: Mapped[User | None] = relationship(back_populates="summaries")
    team: Mapped[Team | None] = relationship(back_populates="summaries")
    review: Mapped[ProgressReview | None] = relationship(back_populates="summary", cascade="all, delete-orphan")


class ProgressReview(TimestampMixin, Base):
    __tablename__ = "progress_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    summary_id: Mapped[int] = mapped_column(ForeignKey("activity_summaries.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    review: Mapped[str] = mapped_column(String(80), index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    explanation: Mapped[str] = mapped_column(Text, default="")

    summary: Mapped[ActivitySummary] = relationship(back_populates="review")


class OutsideWorkNote(TimestampMixin, Base):
    __tablename__ = "outside_work_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    checkpoint_id: Mapped[int] = mapped_column(ForeignKey("checkpoints.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    repository_id: Mapped[int | None] = mapped_column(ForeignKey("repositories.id"), nullable=True, index=True)
    note: Mapped[str] = mapped_column(Text)

    checkpoint: Mapped[Checkpoint] = relationship(back_populates="outside_work_notes")
    user: Mapped[User | None] = relationship()
    team: Mapped[Team | None] = relationship()
    repository: Mapped[Repository | None] = relationship()
