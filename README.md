# gitswarm

GitHub project progress tracker MVP for weekly checkpoint reports across repositories and their scanned team members.

## What it does

- Connects to GitHub with `GITHUB_TOKEN`.
- Stores repositories, repository-backed teams, commit authors, checkpoints, activity, summaries, reviews, and outside-work notes.
- Ingests full repository commit history when a repository is added or refreshed.
- Grades checkpoint intervals from the locally cached commit history.
- Generates explainable red/yellow/green progress reviews using deterministic heuristics.
- Serves a simple FastAPI admin dashboard.
- Exports weekly reports as Markdown and PDF.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a `.env` file:

```dotenv
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/gitswarm
GITHUB_TOKEN=ghp_your_token
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me
SECRET_KEY=change-me-too
```

Or set environment variables in PowerShell:

```powershell
$env:DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/gitswarm"
$env:GITHUB_TOKEN="ghp_your_token"
$env:ADMIN_USERNAME="admin"
$env:ADMIN_PASSWORD="change-me"
$env:SECRET_KEY="change-me-too"
```

## Discord terminal assistant

Create a Discord application and bot in the [Discord Developer Portal](https://discord.com/developers/applications):

1. Open the app's **Bot** settings and copy or reset its bot token. Treat it like a password; do not commit it or paste it into chat.
2. In **Bot → Privileged Gateway Intents**, enable **Server Members Intent**. The assistant needs this to retrieve the server roster and names. Leave **Presence Intent** and **Message Content Intent** off.
3. Under **Installation** (or **OAuth2 → URL Generator**), install the app with the `bot` scope in the server. It does not need channel permissions for roster lookup or direct messages, so leave the bot permissions empty. The bot must be in the server whose members you want to list.
4. Put the token in `.env`:

```dotenv
DISCORD_BOT_TOKEN=your_bot_token
```

The Server Members Intent switch grants the roster access; an OAuth permission checkbox does not. Discord's privileged intent documentation says this intent covers member events and requests for guild member lists. Apps below 10,000 users can enable it in the Developer Portal; above that threshold Discord requires an access review. See [Discord's privileged intent guide](https://support-dev.discord.com/hc/en-us/articles/6207308062871-What-are-Privileged-Intents-) and [Gateway intents reference](https://discord.com/developers/docs/topics/gateway#gateway-intents).

Get the names and IDs of server members from the terminal:

```bash
python -m discord_assistant members --guild-id 987654321098765432
python -m discord_assistant members --guild-id 987654321098765432 --contains alex
```

The columns are server display name, account username, and user ID. Use the ID to target a message; it is unique even if several people use the same display name. To find the server ID, enable **User Settings → Advanced → Developer Mode**, then right-click the server and choose **Copy Server ID**.

Send a direct message:

```bash
python -m discord_assistant send --user-id 123456789012345678 --message "Hi from the terminal"
```

Attach one or more files with repeatable `--file` options:

```bash
python -m discord_assistant send --user-id 123456789012345678 \
  --message "Here are the files" --file ./report.pdf --file ./notes.txt
```

For longer messages, read UTF-8 text from a file or standard input:

```bash
python -m discord_assistant send --user-id 123456789012345678 --message-file ./message.txt
cat message.txt | python -m discord_assistant send --user-id 123456789012345678 --message-file -
```

The existing GitSwarm CLI also accepts these under `python -m app.cli discord members ...` and `python -m app.cli discord send ...`. A bot can only DM a user when Discord allows the DM; the user may have server DM privacy settings or other restrictions that prevent it. This uses a bot account and does not automate a personal account.

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

Open `http://127.0.0.1:8000`, log in with `ADMIN_USERNAME` and `ADMIN_PASSWORD`, then add repositories. Each repository creates a team and ingests full commit history. Team membership is based on GitHub-linked commit authors and is reconciled whenever commit history is refreshed.

## Grade an interval

```powershell
python -m app.cli ingest --since 2026-05-01T09:00:00 --until 2026-05-08T09:00:00
```

You can also pass an explicit organization:

```powershell
python -m app.cli ingest --org my-org --since 2026-05-01T09:00:00 --until 2026-05-08T09:00:00
```

From the web app, open **Repositories** and use **Repository checkup** to select one repository, choose a date range, and either open the checkup or generate a Markdown/PDF report.

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
- Repository commit history is cached first. Checkpoints grade any requested interval from that cached history.
- Authentication is a single admin login backed by environment variables.
- PDF export uses WeasyPrint when available. If native PDF dependencies are missing, the app writes a readable `.pdf` fallback containing the report text.
