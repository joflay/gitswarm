from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from requests import HTTPError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_db, init_db
from app.github_client import GitHubClient
from app.models import ActivitySummary, Checkpoint, Commit, OutsideWorkNote, Repository, Team, User
from app.services.checkups import REPOSITORY_CHECKUP_NOTE_PREFIX
from app.services.ingest import HISTORY_CHECKPOINT_DAY_TIME, ingest_activity, ingest_repository_checkup, refresh_checkpoint_activity, refresh_repository_commit_history
from app.services.reports import export_report
from app.services.summary import rebuild_summaries


settings = get_settings()
app = FastAPI(title="GitSwarm")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
serializer = URLSafeSerializer(settings.secret_key, salt="gitswarm-session")
REFRESH_EXCLUDED_REPOSITORIES = {"options updater", "stocks update", "options model"}


@app.on_event("startup")
def startup() -> None:
    init_db()


def require_admin(request: Request) -> None:
    token = request.cookies.get("gitswarm_session")
    if not token:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    try:
        username = serializer.loads(token)
    except BadSignature as exc:
        raise HTTPException(status_code=303, headers={"Location": "/login"}) from exc
    if username != settings.admin_username:
        raise HTTPException(status_code=303, headers={"Location": "/login"})


@app.get("/login")
def login_page(request: Request) -> Response:
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)) -> Response:
    if username != settings.admin_username or password != settings.admin_password:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"}, status_code=401)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("gitswarm_session", serializer.dumps(username), httponly=True, samesite="lax")
    return response


@app.get("/logout")
def logout_get() -> Response:
    return logout()


@app.post("/logout")
def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("gitswarm_session")
    return response


@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    return templates.TemplateResponse("dashboard.html", _dashboard_context(request, db))


def _dashboard_context(request: Request, db: Session, error: str = "") -> dict[str, object]:
    checkpoints = (
        db.query(Checkpoint)
        .filter(Checkpoint.checkpoint_day_time != HISTORY_CHECKPOINT_DAY_TIME)
        .filter(~Checkpoint.notes.startswith(REPOSITORY_CHECKUP_NOTE_PREFIX))
        .order_by(Checkpoint.until.desc())
        .all()
    )
    checkpoint = checkpoints[0] if checkpoints else None
    summaries = []
    if checkpoint:
        summaries = (
            db.query(ActivitySummary)
            .options(joinedload(ActivitySummary.review))
            .filter_by(checkpoint_id=checkpoint.id)
            .filter_by(scope="repo")
            .order_by(ActivitySummary.subject)
            .all()
        )
    return {
        "request": request,
        "checkpoints": checkpoints,
        "checkpoint": checkpoint,
        "checkpoint_form": _checkpoint_form_context(db, checkpoint),
        "recent_checkpoints": checkpoints[:5],
        "summaries": summaries,
        "teams": db.query(Team).order_by(Team.name).all(),
        "repos": db.query(Repository).order_by(Repository.full_name).all(),
        "users": db.query(User).order_by(User.github_username).all(),
        "error": error,
    }


def _checkpoint_form_context(db: Session, checkpoint: Checkpoint | None, now: datetime | None = None) -> dict[str, object]:
    org_options = [
        org_name
        for (org_name,) in db.query(Repository.org_name)
        .filter_by(is_active=True)
        .distinct()
        .order_by(Repository.org_name)
        .all()
    ]
    current_time = (now or datetime.utcnow()).replace(second=0, microsecond=0)
    if checkpoint:
        since = checkpoint.until.replace(second=0, microsecond=0)
        until = since + timedelta(days=7)
        selected_org = checkpoint.org_name
        checkpoint_day_time = checkpoint.checkpoint_day_time or until.strftime("%A %H:%M")
    else:
        until = current_time
        since = until - timedelta(days=7)
        selected_org = org_options[0] if org_options else ""
        checkpoint_day_time = until.strftime("%A %H:%M")
    if selected_org not in org_options and org_options:
        selected_org = org_options[0]
    return {
        "org_options": org_options,
        "selected_org": selected_org,
        "since": since.strftime("%Y-%m-%dT%H:%M"),
        "until": until.strftime("%Y-%m-%dT%H:%M"),
        "checkpoint_day_time": checkpoint_day_time,
    }


def _ensure_team_for_repo(db: Session, repo: Repository) -> Team:
    team = db.query(Team).filter_by(name=repo.full_name).one_or_none()
    if not team:
        team = Team(name=repo.full_name)
        db.add(team)
        db.flush()
    return team


def _delete_team(db: Session, team: Team) -> None:
    for member in list(team.members):
        team.members.remove(member)
        if member.team_id == team.id:
            member.team_id = next((other.id for other in member.teams), None)
    db.query(ActivitySummary).filter_by(team_id=team.id).update({"team_id": None}, synchronize_session=False)
    db.query(OutsideWorkNote).filter_by(team_id=team.id).update({"team_id": None}, synchronize_session=False)
    db.delete(team)
    db.commit()
    db.expire_all()


def _github_error_detail(exc: HTTPError) -> str:
    response = exc.response
    if response is None:
        return str(exc)
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()
    message = payload.get("message", "")
    documentation_url = payload.get("documentation_url", "")
    details = [message, documentation_url]
    return " ".join(detail for detail in details if detail).strip()


def _parse_datetime_input(value: str, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO datetime, like 2026-05-01T09:00:00") from exc


@app.get("/teams")
def teams_index(request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    teams = db.query(Team).options(joinedload(Team.members)).order_by(Team.name).all()
    unassigned_members = db.query(User).filter_by(team_id=None).order_by(User.github_username).all()
    return templates.TemplateResponse(
        "teams.html",
        {
            "request": request,
            "teams": teams,
            "unassigned_members": unassigned_members,
        },
    )


@app.get("/teams/{team_id}")
def team_detail(team_id: int, request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    team = db.query(Team).options(joinedload(Team.members)).filter_by(id=team_id).one_or_none()
    if not team:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "team_detail.html",
        {
            "request": request,
            "team": team,
        },
    )


@app.post("/teams/{team_id}/delete")
def delete_team(team_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404)
    _delete_team(db, team)
    return RedirectResponse("/teams", status_code=303)


@app.get("/members")
def members_index(request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    users = db.query(User).options(joinedload(User.team)).order_by(User.github_username).all()
    return templates.TemplateResponse(
        "members.html",
        {
            "request": request,
            "users": users,
            "user_scores": _latest_member_scores(db, users),
            "teams": db.query(Team).order_by(Team.name).all(),
        },
    )


def _latest_member_scores(db: Session, users: list[User]) -> dict[int, int]:
    checkpoint = (
        db.query(Checkpoint)
        .filter(Checkpoint.checkpoint_day_time != HISTORY_CHECKPOINT_DAY_TIME)
        .filter(~Checkpoint.notes.startswith(REPOSITORY_CHECKUP_NOTE_PREFIX))
        .order_by(Checkpoint.until.desc(), Checkpoint.id.desc())
        .first()
    )
    if not checkpoint or not users:
        return {}

    user_ids = [user.id for user in users]
    ids_by_username = {user.github_username: user.id for user in users}
    summaries = (
        db.query(ActivitySummary)
        .options(joinedload(ActivitySummary.review))
        .filter_by(checkpoint_id=checkpoint.id, scope="member")
        .filter((ActivitySummary.user_id.in_(user_ids)) | (ActivitySummary.subject.in_(ids_by_username.keys())))
        .all()
    )

    scores: dict[int, int] = {}
    for summary in summaries:
        user_id = summary.user_id or ids_by_username.get(summary.subject)
        if user_id and summary.review:
            scores[user_id] = summary.review.score
    return scores


@app.get("/repositories")
def repositories_index(request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    error = ""
    if request.query_params.get("error") == "duplicate-repo":
        error = "A repository or generated team with that name already exists."
    return _repositories_response(request, db, error=error)


def _repositories_response(request: Request, db: Session, error: str = "", notice: str = "") -> Response:
    repos = db.query(Repository).order_by(Repository.full_name).all()
    repo_cards = [_repository_card_context(db, repo) for repo in repos]
    org_names = sorted({repo.org_name for repo in repos if repo.is_active})
    return templates.TemplateResponse(
        "repositories.html",
        {
            "request": request,
            "repo_cards": repo_cards,
            "org_names": org_names,
            "default_org": org_names[0] if len(org_names) == 1 else "",
            "error": error,
            "notice": notice,
        },
    )


def _normalized_repository_name(name: str) -> str:
    return " ".join(name.casefold().replace("_", " ").replace("-", " ").split())


@app.post("/repositories/sync-from-github")
def sync_org_repositories(
    request: Request,
    org_name: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    org_name = org_name.strip()
    if not org_name or "/" in org_name:
        return _repositories_response(request, db, error="Enter a GitHub organization name, without a slash.")

    try:
        full_names = GitHubClient(settings.github_token).list_repositories(org_name)
    except HTTPError as exc:
        response = exc.response
        status_code = response.status_code if response is not None else "unknown"
        detail = _github_error_detail(exc)
        return _repositories_response(
            request,
            db,
            error=f"Could not list repositories for {org_name} (HTTP {status_code}). {detail}".strip(),
        )
    except ValueError as exc:
        return _repositories_response(request, db, error=str(exc))

    added: list[str] = []
    refreshed: list[tuple[str, int]] = []
    skipped: list[str] = []
    inactive: list[str] = []
    failures: list[str] = []
    for full_name in full_names:
        owner, _, name = full_name.partition("/")
        if owner.casefold() != org_name.casefold():
            continue
        if _normalized_repository_name(name) in REFRESH_EXCLUDED_REPOSITORIES:
            skipped.append(full_name)
            continue

        repo = db.query(Repository).filter_by(full_name=full_name).one_or_none()
        if repo is None:
            repo = Repository(org_name=owner, name=name, full_name=full_name)
            db.add(repo)
            db.flush()
            _ensure_team_for_repo(db, repo)
            db.commit()
            added.append(full_name)
        elif not repo.is_active:
            inactive.append(full_name)
            continue

        try:
            commit_count = refresh_repository_commit_history(db, repo)
            refreshed.append((full_name, commit_count))
        except HTTPError as exc:
            db.rollback()
            response = exc.response
            status_code = response.status_code if response is not None else "unknown"
            detail = _github_error_detail(exc)
            failures.append(f"{full_name}: HTTP {status_code} {detail}".strip())
        except ValueError as exc:
            db.rollback()
            failures.append(f"{full_name}: {exc}")

    total_commits = sum(count for _, count in refreshed)
    notice_parts = [f"Discovered {len(full_names)} repositories in {org_name}."]
    notice_parts.append(f"Added {len(added)} and refreshed {len(refreshed)} ({total_commits} commits).")
    if skipped:
        notice_parts.append(f"Excluded: {', '.join(skipped)}.")
    if inactive:
        notice_parts.append(f"Left inactive: {', '.join(inactive)}.")
    if added:
        notice_parts.append(f"Added: {', '.join(added)}.")
    if refreshed:
        notice_parts.append("Refreshed: " + ", ".join(name for name, _ in refreshed) + ".")
    error = "Some repositories could not be refreshed: " + "; ".join(failures) if failures else ""
    return _repositories_response(request, db, error=error, notice=" ".join(notice_parts))


@app.post("/repositories/refresh-all-commit-history")
def refresh_all_repo_commit_history(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    repositories = db.query(Repository).filter_by(is_active=True).order_by(Repository.full_name).all()
    skipped: list[str] = []
    targets: list[Repository] = []
    for repo in repositories:
        if _normalized_repository_name(repo.name) in REFRESH_EXCLUDED_REPOSITORIES:
            skipped.append(repo.full_name)
        else:
            targets.append(repo)

    refreshed: list[tuple[str, int]] = []
    failures: list[str] = []
    for repo in targets:
        try:
            commit_count = refresh_repository_commit_history(db, repo)
            refreshed.append((repo.full_name, commit_count))
        except HTTPError as exc:
            db.rollback()
            response = exc.response
            status_code = response.status_code if response is not None else "unknown"
            detail = _github_error_detail(exc)
            failures.append(f"{repo.full_name}: HTTP {status_code} {detail}".strip())
        except ValueError as exc:
            db.rollback()
            failures.append(f"{repo.full_name}: {exc}")

    total_commits = sum(count for _, count in refreshed)
    notice_parts = [f"Refreshed {len(refreshed)} repositories ({total_commits} commits)."]
    if skipped:
        notice_parts.append(f"Skipped excluded repositories: {', '.join(skipped)}.")
    if not targets:
        notice_parts[0] = "No active repositories to refresh after applying exclusions."
    error = "Refresh failed for: " + "; ".join(failures) if failures else ""
    if refreshed:
        notice_parts.append("Updated: " + ", ".join(name for name, _ in refreshed) + ".")
    return _repositories_response(request, db, error=error, notice=" ".join(notice_parts))


@app.post("/repositories/{repository_id}/checkup")
def create_repository_checkup(
    repository_id: int,
    request: Request,
    since: str = Form(...),
    until: str = Form(...),
    report_format: str = Form("view"),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404)
    try:
        checkpoint = ingest_repository_checkup(db, repo, _parse_datetime_input(since, "Since"), _parse_datetime_input(until, "Until"))
    except ValueError as exc:
        return _repository_detail_response(request, db, repo, error=str(exc))

    if report_format in {"markdown", "pdf"}:
        path = export_report(db, checkpoint, report_format)
        return FileResponse(path)
    return RedirectResponse(f"/checkpoint/{checkpoint.id}", status_code=303)


def _repository_card_context(db: Session, repo: Repository) -> dict[str, object]:
    team = db.query(Team).filter_by(name=repo.full_name).one_or_none()
    commit_count = db.query(Commit).filter_by(repository_id=repo.id).count()
    latest_commit = db.query(Commit).filter_by(repository_id=repo.id).order_by(Commit.committed_at.desc()).first()
    member_count = len(team.members) if team else 0
    health = _repository_health_context(db, repo)
    return {
        "repo": repo,
        "team": team,
        "member_count": member_count,
        "commit_count": commit_count,
        "latest_commit": latest_commit,
        "health": health,
    }


def _latest_repository_checkup(db: Session, repo: Repository) -> Checkpoint | None:
    return (
        db.query(Checkpoint)
        .filter_by(notes=f"{REPOSITORY_CHECKUP_NOTE_PREFIX}{repo.full_name}")
        .filter(Checkpoint.checkpoint_day_time != HISTORY_CHECKPOINT_DAY_TIME)
        .order_by(Checkpoint.until.desc(), Checkpoint.id.desc())
        .first()
    )


def _repository_health_context(db: Session, repo: Repository) -> dict[str, object]:
    checkpoint = _latest_repository_checkup(db, repo)
    if not checkpoint:
        return {
            "checkpoint": None,
            "repo_summary": None,
            "member_summaries": [],
        }
    summaries = (
        db.query(ActivitySummary)
        .options(joinedload(ActivitySummary.review))
        .filter_by(checkpoint_id=checkpoint.id)
        .order_by(ActivitySummary.scope, ActivitySummary.subject)
        .all()
    )
    repo_summary = next((summary for summary in summaries if summary.scope == "repo" and summary.repository_id == repo.id), None)
    member_summaries = [summary for summary in summaries if summary.scope == "member"]
    return {
        "checkpoint": checkpoint,
        "repo_summary": repo_summary,
        "member_summaries": member_summaries,
    }


def _repository_detail_response(request: Request, db: Session, repo: Repository, error: str = "", notice: str = "") -> Response:
    team = db.query(Team).options(joinedload(Team.members)).filter_by(name=repo.full_name).one_or_none()
    commits = db.query(Commit).options(joinedload(Commit.author)).filter_by(repository_id=repo.id).order_by(Commit.committed_at.desc()).limit(100).all()
    commit_count = db.query(Commit).filter_by(repository_id=repo.id).count()
    first_commit = db.query(Commit).filter_by(repository_id=repo.id).order_by(Commit.committed_at.asc()).first()
    latest_commit = commits[0] if commits else None
    active_days = db.query(func.count(func.distinct(func.date(Commit.committed_at)))).filter_by(repository_id=repo.id).scalar() or 0
    contributors = _repository_contributors(db, repo)
    canonical_members = [member for member in (team.members if team else []) if member.canonical_user_id is None]
    alias_members = [member for member in (team.members if team else []) if member.canonical_user_id is not None]
    return templates.TemplateResponse(
        "repository_detail.html",
        {
            "request": request,
            "repo": repo,
            "team": team,
            "members": canonical_members,
            "alias_members": alias_members,
            "commits": commits,
            "commit_count": commit_count,
            "first_commit": first_commit,
            "latest_commit": latest_commit,
            "active_days": active_days,
            "contributors": contributors,
            "health": _repository_health_context(db, repo),
            "error": error,
            "notice": notice,
        },
    )


def _display_user(user: User | None, fallback: str = "") -> str:
    if not user:
        return fallback
    canonical = user.canonical_user or user
    if canonical.id == user.id:
        alias_names = sorted(alias.github_username for alias in canonical.aliases)
        if alias_names:
            return f"{canonical.github_username} ({', '.join(alias_names)})"
        return canonical.github_username
    return canonical.github_username


def _repository_contributors(db: Session, repo: Repository) -> list[dict[str, object]]:
    commits = (
        db.query(Commit)
        .options(joinedload(Commit.author).joinedload(User.canonical_user), joinedload(Commit.author).joinedload(User.aliases))
        .filter_by(repository_id=repo.id)
        .all()
    )
    users_by_login = {user.github_username.lower(): user for user in db.query(User).options(joinedload(User.canonical_user), joinedload(User.aliases)).all()}
    grouped: dict[str, dict[str, object]] = {}
    for commit in commits:
        author = commit.author or users_by_login.get(commit.author_login.lower())
        key = str((author.canonical_user_id or author.id) if author else commit.author_login.lower())
        row = grouped.setdefault(
            key,
            {
                "name": _display_user(author, commit.author_login or "Unknown"),
                "raw_authors": set(),
                "commit_count": 0,
            },
        )
        row["commit_count"] += 1
        row["raw_authors"].add(commit.author_login or "Unknown")
    contributors = sorted(grouped.values(), key=lambda row: (-row["commit_count"], row["name"]))
    for row in contributors:
        row["raw_authors"] = sorted(row["raw_authors"])
    return contributors[:10]


@app.get("/repositories/{repository_id}")
def repository_detail(repository_id: int, request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404)
    return _repository_detail_response(request, db, repo)


@app.post("/repositories/{repository_id}/aliases")
def merge_member_alias(
    repository_id: int,
    canonical_user_id: int = Form(...),
    alias_user_id: str = Form(""),
    raw_author_login: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404)
    canonical = db.get(User, canonical_user_id)
    if not canonical:
        raise HTTPException(status_code=404)
    target_id = canonical.canonical_user_id or canonical.id
    target_user = db.get(User, target_id) or canonical

    if alias_user_id:
        alias = db.get(User, int(alias_user_id))
        if not alias:
            raise HTTPException(status_code=404)
        if canonical.id != alias.id:
            alias.canonical_user_id = target_id
            for team in list(alias.teams):
                if team not in target_user.teams:
                    target_user.teams.append(team)
                alias.teams.remove(team)
            if target_user.team_id is None and target_user.teams:
                target_user.team_id = target_user.teams[0].id
            alias.team_id = target_user.team_id

    raw_author_login = raw_author_login.strip()
    if raw_author_login:
        db.query(Commit).filter(func.lower(Commit.author_login) == raw_author_login.lower()).update({"author_id": target_id}, synchronize_session=False)

    if alias_user_id or raw_author_login:
        db.commit()
    return RedirectResponse(f"/repositories/{repository_id}", status_code=303)


@app.get("/checkpoint/{checkpoint_id}")
def checkpoint_view(checkpoint_id: int, request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    return _checkpoint_response(checkpoint_id, request, db)


def _checkpoint_response(checkpoint_id: int, request: Request, db: Session, error: str = "") -> Response:
    checkpoint = db.get(Checkpoint, checkpoint_id)
    if not checkpoint or checkpoint.checkpoint_day_time == HISTORY_CHECKPOINT_DAY_TIME:
        raise HTTPException(status_code=404)
    title = f"{checkpoint.org_name} checkpoint"
    if checkpoint.notes.startswith(REPOSITORY_CHECKUP_NOTE_PREFIX):
        title = f"{checkpoint.notes.removeprefix(REPOSITORY_CHECKUP_NOTE_PREFIX)} checkup"
    summaries = (
        db.query(ActivitySummary)
        .options(joinedload(ActivitySummary.review))
        .filter_by(checkpoint_id=checkpoint.id)
        .order_by(ActivitySummary.scope, ActivitySummary.subject)
        .all()
    )
    notes = db.query(OutsideWorkNote).filter_by(checkpoint_id=checkpoint.id).all()
    return templates.TemplateResponse("checkpoint.html", {"request": request, "checkpoint": checkpoint, "title": title, "summaries": summaries, "notes": notes, "error": error})


@app.post("/checkpoint/{checkpoint_id}/refresh")
def refresh_checkpoint(checkpoint_id: int, request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    checkpoint = db.get(Checkpoint, checkpoint_id)
    if not checkpoint:
        raise HTTPException(status_code=404)
    try:
        refresh_checkpoint_activity(db, checkpoint)
    except ValueError as exc:
        return _checkpoint_response(checkpoint_id, request, db, str(exc))
    return RedirectResponse(f"/checkpoint/{checkpoint_id}", status_code=303)


@app.post("/config/repo")
def add_repo(request: Request, org_name: str = Form(...), name: str = Form(...), db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    org_name = org_name.strip()
    name = name.strip()
    full_name = f"{org_name}/{name}"
    repo = db.query(Repository).filter_by(full_name=full_name).one_or_none()
    if not repo:
        repo = Repository(org_name=org_name, name=name, full_name=full_name)
        db.add(repo)
        db.flush()
    else:
        repo.is_active = True
    _ensure_team_for_repo(db, repo)
    db.commit()
    try:
        commit_count = refresh_repository_commit_history(db, repo)
    except HTTPError as exc:
        response = exc.response
        target = response.url.split("?", 1)[0] if response is not None else "GitHub API"
        status_code = response.status_code if response is not None else "unknown"
        detail = _github_error_detail(exc)
        message = f"Repository was saved, but commit history ingestion failed with status {status_code} for {target}. {detail}"
        return _repositories_response(request, db, error=message)
    except ValueError as exc:
        return _repositories_response(request, db, error=f"Repository was saved, but commit history ingestion failed: {exc}")
    return _repositories_response(request, db, notice=f"Added {repo.full_name} and ingested {commit_count} commits.")


@app.post("/repositories/{repository_id}")
def update_repo(
    repository_id: int,
    org_name: str = Form(...),
    name: str = Form(...),
    relevance_rules: str = Form(""),
    is_active: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404)
    old_full_name = repo.full_name
    repo.org_name = org_name.strip()
    repo.name = name.strip()
    repo.full_name = f"{repo.org_name}/{repo.name}"
    repo.relevance_rules = relevance_rules.strip()
    repo.is_active = is_active == "on"
    team = db.query(Team).filter_by(name=old_full_name).one_or_none()
    if team:
        team.name = repo.full_name
    else:
        _ensure_team_for_repo(db, repo)
    history_checkpoint = db.query(Checkpoint).filter_by(notes=f"commit-history:{old_full_name}").one_or_none()
    if history_checkpoint:
        history_checkpoint.org_name = repo.org_name
        history_checkpoint.notes = f"commit-history:{repo.full_name}"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/repositories?error=duplicate-repo", status_code=303)
    return RedirectResponse(f"/repositories/{repository_id}", status_code=303)


@app.post("/repositories/{repository_id}/refresh-commit-history")
def refresh_repo_commit_history(
    repository_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404)

    try:
        commit_count = refresh_repository_commit_history(db, repo)
    except HTTPError as exc:
        response = exc.response
        target = response.url.split("?", 1)[0] if response is not None else "GitHub API"
        status_code = response.status_code if response is not None else "unknown"
        detail = _github_error_detail(exc)
        message = f"Commit history refresh failed with status {status_code} for {target}. {detail}"
        return _repository_detail_response(request, db, repo, error=message)
    except ValueError as exc:
        return _repository_detail_response(request, db, repo, error=str(exc))

    return _repository_detail_response(request, db, repo, notice=f"Refreshed {commit_count} commits for {repo.full_name}.")


@app.post("/repositories/{repository_id}/reset-data")
def reset_repo_data(
    repository_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404)

    team = db.query(Team).options(joinedload(Team.members)).filter_by(name=repo.full_name).one_or_none()
    if team:
        for member in list(team.members):
            team.members.remove(member)
            if member.team_id == team.id:
                member.team_id = next((other.id for other in member.teams), None)
    db.query(Checkpoint).filter_by(notes=f"commit-history:{repo.full_name}", checkpoint_day_time=HISTORY_CHECKPOINT_DAY_TIME).delete(synchronize_session=False)
    db.query(Commit).filter_by(repository_id=repo.id).delete(synchronize_session=False)
    db.commit()

    try:
        commit_count = refresh_repository_commit_history(db, repo)
    except HTTPError as exc:
        response = exc.response
        target = response.url.split("?", 1)[0] if response is not None else "GitHub API"
        status_code = response.status_code if response is not None else "unknown"
        detail = _github_error_detail(exc)
        message = f"Repository data was reset, but refresh failed with status {status_code} for {target}. {detail}"
        return _repository_detail_response(request, db, repo, error=message)
    except ValueError as exc:
        return _repository_detail_response(request, db, repo, error=f"Repository data was reset, but refresh failed: {exc}")

    notice = f"Reset {repo.full_name}, refreshed {commit_count} commits, and synced its team from commit authors."
    return _repository_detail_response(request, db, repo, notice=notice)


@app.post("/checkpoint/manual")
def add_manual_checkpoint(
    request: Request,
    org_name: str = Form(...),
    since: str = Form(...),
    until: str = Form(...),
    checkpoint_day_time: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    try:
        since_date = datetime.fromisoformat(since)
        until_date = datetime.fromisoformat(until)
        if until_date <= since_date:
            raise ValueError("To must be after From")
        checkpoint = ingest_activity(db, org_name.strip() or None, since_date, until_date, checkpoint_day_time)
    except HTTPError as exc:
        response = exc.response
        target = response.url.split("?", 1)[0] if response is not None else "GitHub API"
        status_code = response.status_code if response is not None else "unknown"
        detail = _github_error_detail(exc)
        message = f"GitHub ingestion failed with status {status_code} for {target}. {detail}"
        return templates.TemplateResponse("dashboard.html", _dashboard_context(request, db, message), status_code=502)
    except ValueError as exc:
        return templates.TemplateResponse("dashboard.html", _dashboard_context(request, db, str(exc)), status_code=400)
    return RedirectResponse(f"/checkpoint/{checkpoint.id}", status_code=303)


@app.post("/notes")
def add_note(
    checkpoint_id: int = Form(...),
    note: str = Form(...),
    user_id: str = Form(""),
    team_id: str = Form(""),
    repository_id: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    checkpoint = db.get(Checkpoint, checkpoint_id)
    if not checkpoint:
        raise HTTPException(status_code=404)
    db.add(
        OutsideWorkNote(
            checkpoint_id=checkpoint_id,
            user_id=int(user_id) if user_id else None,
            team_id=int(team_id) if team_id else None,
            repository_id=int(repository_id) if repository_id else None,
            note=note,
        )
    )
    db.commit()
    rebuild_summaries(db, checkpoint)
    return RedirectResponse(f"/checkpoint/{checkpoint_id}", status_code=303)


@app.get("/reports/{checkpoint_id}.{fmt}")
def download_report(checkpoint_id: int, fmt: str, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    if fmt == "md":
        fmt = "markdown"
    if fmt not in {"markdown", "pdf"}:
        raise HTTPException(status_code=404)
    checkpoint = db.get(Checkpoint, checkpoint_id)
    if not checkpoint:
        raise HTTPException(status_code=404)
    path = export_report(db, checkpoint, fmt)
    return FileResponse(path)
