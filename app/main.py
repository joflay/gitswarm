from __future__ import annotations

from datetime import datetime

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
from app.services.ingest import HISTORY_CHECKPOINT_DAY_TIME, ingest_activity, refresh_checkpoint_activity, refresh_repository_commit_history
from app.services.reports import export_report
from app.services.summary import rebuild_summaries


settings = get_settings()
app = FastAPI(title="GitSwarm")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
serializer = URLSafeSerializer(settings.secret_key, salt="gitswarm-session")


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
    checkpoints = db.query(Checkpoint).filter(Checkpoint.checkpoint_day_time != HISTORY_CHECKPOINT_DAY_TIME).order_by(Checkpoint.until.desc()).all()
    checkpoint = checkpoints[0] if checkpoints else None
    summaries = []
    if checkpoint:
        summaries = (
            db.query(ActivitySummary)
            .options(joinedload(ActivitySummary.review))
            .filter_by(checkpoint_id=checkpoint.id)
            .order_by(ActivitySummary.scope, ActivitySummary.subject)
            .all()
        )
    return {
        "request": request,
        "checkpoints": checkpoints,
        "checkpoint": checkpoint,
        "summaries": summaries,
        "teams": db.query(Team).order_by(Team.name).all(),
        "repos": db.query(Repository).order_by(Repository.full_name).all(),
        "users": db.query(User).order_by(User.github_username).all(),
        "error": error,
    }


def _ensure_team_for_repo(db: Session, repo: Repository) -> Team:
    team = db.query(Team).filter_by(name=repo.full_name).one_or_none()
    if not team:
        team = Team(name=repo.full_name)
        db.add(team)
        db.flush()
    return team


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


@app.get("/members")
def members_index(request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    users = db.query(User).options(joinedload(User.team)).order_by(User.github_username).all()
    return templates.TemplateResponse(
        "members.html",
        {
            "request": request,
            "users": users,
            "teams": db.query(Team).order_by(Team.name).all(),
        },
    )


@app.get("/repositories")
def repositories_index(request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    error = ""
    if request.query_params.get("error") == "duplicate-repo":
        error = "A repository or generated team with that name already exists."
    return _repositories_response(request, db, error=error)


def _repositories_response(request: Request, db: Session, error: str = "", notice: str = "") -> Response:
    repos = db.query(Repository).order_by(Repository.full_name).all()
    repo_cards = [_repository_card_context(db, repo) for repo in repos]
    return templates.TemplateResponse("repositories.html", {"request": request, "repo_cards": repo_cards, "error": error, "notice": notice})


def _repository_card_context(db: Session, repo: Repository) -> dict[str, object]:
    team = db.query(Team).filter_by(name=repo.full_name).one_or_none()
    commit_count = db.query(Commit).filter_by(repository_id=repo.id).count()
    latest_commit = db.query(Commit).filter_by(repository_id=repo.id).order_by(Commit.committed_at.desc()).first()
    member_count = len(team.members) if team else 0
    return {
        "repo": repo,
        "team": team,
        "member_count": member_count,
        "commit_count": commit_count,
        "latest_commit": latest_commit,
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
    alias_user_id: int = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404)
    canonical = db.get(User, canonical_user_id)
    alias = db.get(User, alias_user_id)
    if not canonical or not alias:
        raise HTTPException(status_code=404)
    if canonical.id != alias.id:
        alias.canonical_user_id = canonical.canonical_user_id or canonical.id
        alias.team_id = canonical.team_id
        db.commit()
    return RedirectResponse(f"/repositories/{repository_id}", status_code=303)


@app.get("/checkpoint/{checkpoint_id}")
def checkpoint_view(checkpoint_id: int, request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    return _checkpoint_response(checkpoint_id, request, db)


def _checkpoint_response(checkpoint_id: int, request: Request, db: Session, error: str = "") -> Response:
    checkpoint = db.get(Checkpoint, checkpoint_id)
    if not checkpoint or checkpoint.checkpoint_day_time == HISTORY_CHECKPOINT_DAY_TIME:
        raise HTTPException(status_code=404)
    summaries = (
        db.query(ActivitySummary)
        .options(joinedload(ActivitySummary.review))
        .filter_by(checkpoint_id=checkpoint.id)
        .order_by(ActivitySummary.scope, ActivitySummary.subject)
        .all()
    )
    notes = db.query(OutsideWorkNote).filter_by(checkpoint_id=checkpoint.id).all()
    return templates.TemplateResponse("checkpoint.html", {"request": request, "checkpoint": checkpoint, "summaries": summaries, "notes": notes, "error": error})


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


@app.post("/repositories/{repository_id}/sync-collaborators")
def sync_repo_collaborators(
    repository_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404)

    try:
        collaborator_logins = GitHubClient(settings.github_token).list_collaborators(repo.org_name, repo.name)
    except HTTPError as exc:
        response = exc.response
        target = response.url.split("?", 1)[0] if response is not None else "GitHub API"
        status_code = response.status_code if response is not None else "unknown"
        detail = _github_error_detail(exc)
        message = f"GitHub collaborator sync failed with status {status_code} for {target}. {detail}"
        return _repository_detail_response(request, db, repo, error=message)
    except ValueError as exc:
        return _repository_detail_response(request, db, repo, error=str(exc))

    team = _ensure_team_for_repo(db, repo)
    existing_users = {user.github_username.lower(): user for user in db.query(User).all()}
    created = 0
    updated = 0
    for login in collaborator_logins:
        user = existing_users.get(login.lower())
        if not user:
            user = User(github_username=login, team_id=team.id)
            db.add(user)
            existing_users[login.lower()] = user
            created += 1
        else:
            user.is_active = True
            if user.canonical_user_id is None and user.team_id != team.id:
                user.team_id = team.id
                updated += 1
        db.flush()
        db.query(Commit).filter(func.lower(Commit.author_login) == login.lower()).update({"author_id": user.id}, synchronize_session=False)
    db.commit()

    notice = f"Synced {len(collaborator_logins)} collaborators into {team.name}: {created} added, {updated} moved."
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
        checkpoint = ingest_activity(db, org_name.strip() or None, datetime.fromisoformat(since), datetime.fromisoformat(until), checkpoint_day_time)
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
