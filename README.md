# gitswarm

GitHub project progress tracker MVP for weekly checkpoint reports across repositories, teams, and members.

## What it does

- Connects to GitHub with `GITHUB_TOKEN`.
- Stores repositories, teams, members, checkpoints, activity, summaries, reviews, and outside-work notes.
- Ingests commits, changed files, PRs, issues, comments, and reviews for a checkpoint window.
- Generates explainable red/yellow/green progress reviews using deterministic heuristics.
- Serves a simple FastAPI admin dashboard.
- Exports weekly reports as Markdown and PDF.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a `.env` file or set environment variables:

```powershell
$env:DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/gitswarm"
$env:GITHUB_TOKEN="ghp_your_token"
$env:ADMIN_USERNAME="admin"
$env:ADMIN_PASSWORD="change-me"
$env:SECRET_KEY="change-me-too"
```

For quick local experimentation without Postgres, use SQLite:

```powershell
$env:DATABASE_URL="sqlite:///./gitswarm.db"
```

## Initialize the database

```powershell
python -m app.cli init-db
```

## Run the web app

```powershell
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`, log in with `ADMIN_USERNAME` and `ADMIN_PASSWORD`, then configure org, repos, teams, members, and checkpoint settings.

## Ingest GitHub activity

```powershell
python -m app.cli ingest --since 2026-05-01T09:00:00 --until 2026-05-08T09:00:00
```

You can also pass an explicit organization:

```powershell
python -m app.cli ingest --org my-org --since 2026-05-01T09:00:00 --until 2026-05-08T09:00:00
```

## Export reports

```powershell
python -m app.cli report --checkpoint-id 1 --format markdown
python -m app.cli report --checkpoint-id 1 --format pdf
```

Reports are written to `REPORT_OUTPUT_DIR`, defaulting to `./reports`.

## Tests

```powershell
pytest
```

## MVP notes

- Scoring is heuristic-only in this version. It does not call an LLM.
- Manual CLI ingestion is the first checkpoint workflow. The configured checkpoint day/time is stored but not scheduled yet.
- Authentication is a single admin login backed by environment variables.
- PDF export uses WeasyPrint when available. If native PDF dependencies are missing, the app writes a readable `.pdf` fallback containing the report text.
