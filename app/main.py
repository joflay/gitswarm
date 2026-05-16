from __future__ import annotations

from datetime import datetime

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_db, init_db
from app.models import ActivitySummary, Checkpoint, OutsideWorkNote, Repository, Team, User
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
    checkpoints = db.query(Checkpoint).order_by(Checkpoint.until.desc()).all()
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
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "checkpoints": checkpoints,
            "checkpoint": checkpoint,
            "summaries": summaries,
            "teams": db.query(Team).order_by(Team.name).all(),
            "repos": db.query(Repository).order_by(Repository.full_name).all(),
            "users": db.query(User).order_by(User.github_username).all(),
        },
    )


@app.get("/checkpoint/{checkpoint_id}")
def checkpoint_view(checkpoint_id: int, request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    checkpoint = db.get(Checkpoint, checkpoint_id)
    if not checkpoint:
        raise HTTPException(status_code=404)
    summaries = (
        db.query(ActivitySummary)
        .options(joinedload(ActivitySummary.review))
        .filter_by(checkpoint_id=checkpoint.id)
        .order_by(ActivitySummary.scope, ActivitySummary.subject)
        .all()
    )
    notes = db.query(OutsideWorkNote).filter_by(checkpoint_id=checkpoint.id).all()
    return templates.TemplateResponse("checkpoint.html", {"request": request, "checkpoint": checkpoint, "summaries": summaries, "notes": notes})


@app.post("/config/repo")
def add_repo(org_name: str = Form(...), name: str = Form(...), db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    full_name = f"{org_name}/{name}"
    repo = db.query(Repository).filter_by(full_name=full_name).one_or_none()
    if not repo:
        repo = Repository(org_name=org_name, name=name, full_name=full_name)
        db.add(repo)
    else:
        repo.is_active = True
    db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/config/team")
def add_team(name: str = Form(...), db: Session = Depends(get_db), _: None = Depends(require_admin)) -> Response:
    if not db.query(Team).filter_by(name=name).one_or_none():
        db.add(Team(name=name))
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/config/member")
def add_member(
    github_username: str = Form(...),
    display_name: str = Form(""),
    team_id: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    parsed_team_id = int(team_id) if team_id else None
    user = db.query(User).filter_by(github_username=github_username).one_or_none()
    if not user:
        user = User(github_username=github_username, display_name=display_name, team_id=parsed_team_id)
        db.add(user)
    else:
        user.display_name = display_name
        user.team_id = parsed_team_id
    db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/checkpoint/manual")
def add_manual_checkpoint(
    org_name: str = Form(...),
    since: str = Form(...),
    until: str = Form(...),
    checkpoint_day_time: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    checkpoint = Checkpoint(org_name=org_name, since=datetime.fromisoformat(since), until=datetime.fromisoformat(until), checkpoint_day_time=checkpoint_day_time)
    db.add(checkpoint)
    db.commit()
    rebuild_summaries(db, checkpoint)
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
