# MedBot – AI-Powered Medication & Prescription Management Telegram Bot

A production-ready Telegram bot for medication schedule and prescription management, built with Python and aiogram 3. Features a full **function-calling AI agent** (powered by NVIDIA NIM, Llama models) that can read, add, update, and remove the user's medicines and prescriptions directly from a chat conversation — not just answer questions about them. Deployed on Oracle Cloud with a webhook architecture, Docker Compose orchestration, persistent Redis-backed reminders, automated database backups, a web-based admin panel, and a self-hosted CI/CD pipeline with health-gated auto-rollback.

> Bachelor's Diploma Project · NTU "Kharkiv Polytechnic Institute" · 2026

## Try it out

[t.me/tabletkybot](https://t.me/tabletkybot)

![QR Code](assets/qr.jpg)

---

## Features

- **Medication Management** — add, edit (name, dosage, intake time, course length, stock, low-stock threshold), extend, archive, and delete medication schedules with timezone-aware reminders and stock tracking (low-stock alerts, restock flow)
- **Prescription Management** — track prescriptions with expiration dates, allowed quantities, partial-purchase tracking, expiry reminders, and auto-archiving of expired prescriptions
- **AI Agent** — a true function-calling assistant (not just Q&A): it can look up, add, update, and request removal of medicines and prescriptions on the user's behalf, with confirmation prompts before any destructive action
- **Smart, Persistent Reminders** — APScheduler-based notifications with hourly follow-ups for unacknowledged intakes, a per-user repeat-reminders on/off toggle, and misfire grace handling; pending reminders survive container restarts via Redis, preserving the original hourly cadence instead of resetting it
- **Voice Message Support** — send a voice message to the AI agent and it's transcribed (NVIDIA Riva ASR) and handled exactly like a typed question or command
- **Geo-Based Timezone Setup** — users type their city and country and the bot resolves the IANA timezone automatically (geopy + timezonefinder), rescheduling all reminders instantly
- **Reports & Export** — styled Excel (.xlsx) and CSV reports of medication history, including per-medicine adherence statistics
- **Automated Database Backups** — daily `pg_dump` with gzip compression, configurable retention, and optional offsite upload to any S3-compatible storage (e.g. Oracle Object Storage)
- **Admin Panel** — FastAPI + SQLAdmin dashboard with adherence charts, an AI usage-metrics view (latency, tool calls, model used), a live reminder-queue view (pending/unacknowledged reminders with auto-refresh), medicine/prescription/user management (including bot-blocked and active/total user tracking), and a built-in log viewer
- **Event-Driven Sync** — real-time scheduler updates via an internal webhook whenever the admin panel changes data
- **Data Encryption** — sensitive user data (medicine names, prescription names, chat history) encrypted at rest using the `cryptography` library
- **Multilingual** — Ukrainian, English, and Russian interface, plus automatic reply-language detection for the AI assistant based on the user's latest message
- **Security Hardening** — webhook port restricted to Telegram's IP ranges, Basic Auth on the admin panel, SSH/admin access restricted by IP
- **CI/CD Pipeline** — GitHub Actions runs lint (ruff), type checks (mypy), and the pytest suite (92%+ coverage) on every push/PR; a self-hosted-runner deploy workflow then builds and ships to production, polls container health, auto-rolls back to the previous commit on a failed health check, and posts deploy success/failure alerts to Telegram

---

## Tech Stack

| Category | Technologies |
|---|---|
| Language | Python 3.14 |
| Bot Framework | aiogram 3 (async) |
| Web Framework | FastAPI, uvicorn |
| AI / LLM | NVIDIA NIM API (Llama models, function calling) |
| Speech-to-Text | NVIDIA Riva ASR (voice message transcription) |
| ORM | SQLAlchemy 2.0 (async) |
| Database | PostgreSQL (asyncpg driver) |
| Cache / FSM / Persistent State | Redis |
| Scheduling | APScheduler |
| Admin Panel | SQLAdmin (Tabler UI) |
| Reports | openpyxl |
| Geocoding / Timezones | geopy, timezonefinder |
| Backups | pg_dump, boto3 (S3-compatible offsite storage) |
| DevOps | Docker, Docker Compose, Oracle Cloud, GitHub Actions (CI + self-hosted CD runner) |
| Security | cryptography, SSL/TLS, iptables/Security Lists |
| Testing / QA | pytest, pytest-asyncio, pytest-cov (92%+ coverage), ruff, mypy, pip-audit |

---

<details>
<summary><strong>📁 Project Structure</strong> (click to expand)</summary>

```
tgbot/
├── main.py                    # Bot entry point, webhook + internal sync server, scheduler startup
├── config.py                  # Configuration loader (.env)
├── requirements/
│   ├── base.txt               # Runtime dependencies
│   └── dev.txt                # Test/lint/type-check dependencies
├── Dockerfile
├── docker-compose.yml
├── Makefile
├── .env.example
├── certs/                     # Webhook TLS cert/key (local/dev)
├── .github/workflows/
│   ├── ci.yml                  # Lint (ruff), type-check (mypy), pytest w/ coverage
│   └── deploy.yml               # Self-hosted-runner deploy: health-gate, auto-rollback, Telegram alerts
│
├── admin/                      # FastAPI + SQLAdmin panel
│   ├── app.py                   # App/admin instance setup
│   ├── auth.py                  # Basic Auth backend
│   ├── dashboard.py              # Charts page + AI metrics page/API
│   ├── logs_viewer.py            # Live log viewer endpoint
│   ├── model_views.py            # ModelView definitions (User, Medicine, Prescription, ChatHistory, ...)
│   └── sync.py                   # Notifies the bot process of admin-made changes
│
├── web/
│   └── internal_api.py          # Internal webhook endpoints (admin → bot sync, health check)
│
├── handlers/                   # Telegram command and message handlers
│   ├── start.py                  # /start, /help, language selection
│   ├── medicines/                # add, edit, extend, archive, intake, restock, listing, menu, keyboards (FSM-based)
│   ├── prescriptions/            # add, edit, buy, archive, restore, stock, listing, menu, keyboards
│   ├── ai_agent.py               # AI agent chat, voice handling, tool-call confirmations
│   ├── report.py                 # Excel/CSV report generation
│   ├── settings.py               # Name, timezone, language, repeat-reminders toggle, feedback
│   ├── bot_status.py             # Bot-blocked detection
│   ├── common.py                 # Shared user/language resolution helpers
│   └── errors.py                 # Global exception handler
│
├── services/
│   ├── ai_service/               # client, agent loop, prompts, formatting, language detection
│   ├── ai_tools/                 # schemas, dispatcher, helpers, medicine/prescription executors
│   ├── scheduler/                # jobs/ (core, reminders, sync), prescriptions.py, redis_state.py
│   ├── report_service/           # aggregation, excel, csv
│   ├── backup_service.py         # Daily pg_dump + offsite (S3-compatible) upload
│   ├── voice_service.py          # Voice message transcription (NVIDIA Riva ASR) for the AI agent
│   ├── geo_service.py            # City/country → IANA timezone resolution
│   ├── dates.py                  # Date/time formatting helpers
│   └── health.py                 # Health check endpoint logic
│
├── database/
│   ├── models.py                # SQLAlchemy 2.0 models (User, Medicine, Prescription, records, chat history)
│   ├── db.py                    # Async engine and session factory
│   └── crud/                    # users, medicines, prescriptions, chat_history, ai_metrics, stats
│
├── locales/                     # UK/EN/RU localization strings, split by domain
│   ├── texts.py                  # get_text() / btn_variants()
│   ├── _common.py                # Navigation, settings, main menu, start/help text
│   ├── _medicines.py              # Medicine-related strings
│   ├── _prescriptions.py          # Prescription-related strings
│   ├── _ai.py                     # AI assistant strings
│   └── _reports.py                # Report strings
│
├── templates/sqladmin/          # Custom Jinja2 templates (index, ai_metrics, logs, reminders, login, layout)
├── static/                      # Admin panel static assets (favicon, js/chart.js)
├── middleware/
│   ├── db_middleware.py          # DB session injection middleware
│   └── logging_context.py        # Structured logging context per request/update
└── tests/                       # Mirrors the package layout above, 92%+ coverage
```

</details>

---

<details>
<summary><strong>🚀 Getting Started</strong> (click to expand)</summary>

### Option 1 — Docker Compose (Recommended)

**Requirements:** Docker + Docker Compose

```bash
# 1. Configure environment
cp .env.example .env
# Fill in BOT_TOKEN, NVIDIA_API_KEY, DB credentials, and (optionally) backup settings

# 2. Start containers
docker compose up -d

# 3. Check logs
docker compose logs -f bot

# 4. Stop
docker compose down
```

### Option 2 — Local Setup

**Requirements:** Python 3.14, PostgreSQL 15+, Redis

```bash
# 1. Install dependencies
pip install -r requirements/base.txt

# 2. Create database
psql -U postgres
CREATE USER botuser WITH PASSWORD 'botpassword';
CREATE DATABASE medbot OWNER botuser;
\q

# 3. Configure .env and run
python main.py
```

</details>

---

<details>
<summary><strong>⚙️ Configuration</strong> (click to expand)</summary>

### Telegram Bot Token
Get from [@BotFather](https://t.me/BotFather) → `/newbot`

### NVIDIA NIM API (Primary AI, with function calling)
Register at [build.nvidia.com](https://build.nvidia.com), create an API key, and set in `.env`:

```env
NVIDIA_API_KEY=nvapi-...
NVIDIA_MODEL=meta/llama-3.1-70b-instruct
```

### Voice Messages (NVIDIA Riva ASR)
Uses the same NVIDIA account as the NIM API. Set up Riva ASR access at [build.nvidia.com](https://build.nvidia.com) and configure:

```env
NVIDIA_RIVA_SERVER=grpc.nvcf.nvidia.com:443
NVIDIA_RIVA_FUNCTION_ID=...
```
(exact variable names depend on how `voice_service.py` reads its config — check that file / `config.py` for the authoritative list.)

### Database Backups (optional but recommended)
Daily backups run automatically via APScheduler. Local-only by default; offsite upload to any S3-compatible storage (e.g. Oracle Object Storage) is enabled by setting these:

```env
BACKUP_DIR=/app/backups
BACKUP_RETENTION_DAYS=14
BACKUP_S3_BUCKET=medbot-backups
BACKUP_S3_ENDPOINT_URL=https://<namespace>.compat.objectstorage.<region>.oraclecloud.com
BACKUP_S3_ACCESS_KEY=...
BACKUP_S3_SECRET_KEY=...
BACKUP_S3_REGION=eu-frankfurt-1
```

</details>

---

<details>
<summary><strong>📋 Key User Scenarios</strong> (click to expand)</summary>

| Scenario | Action | Result |
|---|---|---|
| Registration | `/start` → Settings ⚙️ | Language selection (UA/EN/RU), name and timezone setup |
| Set timezone | 👤 Settings → 🌍 Change Timezone → type city, country | Geopy + timezonefinder resolve the IANA zone; all reminders reschedule instantly |
| Add medication | 💊 Medicines → ➕ Add | Time validation, scheduler starts, optional stock tracking |
| Edit medication | 💊 Medicines → pick one → ✏️ | Change name, dosage, times, course length, stock, or low-stock threshold individually |
| Toggle repeat reminders | 👤 Settings → 🔁 Repeat reminders | Turns hourly follow-up nudges for missed doses on/off per user |
| Add prescription | 📝 Prescriptions → ➕ Add | Validity period, allowed quantity, expiry reminder configured |
| Ask the AI agent | "Add my Vitamin D, 1000 IU, at 9am for 30 days" | The agent calls the matching tool, adds it, and confirms — no menus needed |
| Ask the AI to remove something | "Delete my ibuprofen" | Agent finds it and shows Archive/Delete/Back buttons for confirmation before acting |
| Reminder received | Wait for scheduled time | Message with ✅ Taken / ⏭️ Skip buttons; hourly follow-ups until acknowledged (if enabled), even across bot restarts |
| Mark prescription purchased | 📝 Prescriptions → Mark bought | Purchased quantity updated, optional stock top-up for the linked medicine |
| Admin change | Edit via web panel | Bot scheduler syncs instantly via internal webhook |
| Ask the AI by voice | Send a voice message | Transcribed via NVIDIA Riva ASR, then handled by the AI agent like any typed message |
| Export data | 📤 Reports | Styled `.xlsx` / `.csv` with full medication history and adherence stats |
| Nightly maintenance | (automatic, 03:00 local time) | Database backed up, compressed, and optionally uploaded offsite |
| Ship a change | Push to `main` | CI lints/type-checks/tests, then the deploy workflow builds, health-gates, and auto-rolls back on failure with a Telegram alert |

</details>

---

<details>
<summary><strong>📦 Dependencies</strong> (click to expand)</summary>

| Library | Purpose |
|---|---|
| aiogram | Async Telegram Bot API framework |
| SQLAlchemy + asyncpg | Async ORM (2.0 style) and PostgreSQL driver |
| APScheduler | Background task scheduling (reminders, sync, backups) |
| openpyxl | Excel report generation |
| aiohttp | Async HTTP requests to AI APIs and internal webhooks |
| pytz / zoneinfo | Timezone handling and validation |
| geopy | Geocoding user-entered city/country to coordinates |
| timezonefinder | Coordinates → IANA timezone lookup |
| fastapi + uvicorn | Admin panel and internal webhooks |
| sqladmin | Web-based admin dashboard |
| cryptography | Encryption of sensitive user data |
| redis | FSM state storage and persistent reminder state |
| boto3 | Offsite database backup uploads (S3-compatible storage) |
| nvidia-riva-client | Voice message transcription (speech-to-text) for the AI agent |
| itsdangerous | Signed session cookies for the admin panel |

</details>
