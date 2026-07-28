# MedBot – Medication & Prescription Management Telegram Bot

A production-ready Telegram bot for medication schedule and prescription management, built with Python and aiogram 3. Deployed on Oracle Cloud with a webhook architecture, Docker Compose orchestration, persistent Redis-backed reminders, automated database backups, a web-based admin panel, and a self-hosted CI/CD pipeline with health-gated auto-rollback.

> Bachelor's Diploma Project · NTU "Kharkiv Polytechnic Institute" · 2026

## Try it out

[t.me/tabletkybot](https://t.me/tabletkybot)

![QR Code](assets/qr.jpg)

---

## Branches

- **`main`** — the active production branch. This is the version described in this README.
- **`legacy-ai`** — an archived snapshot of the bot from when it included a function-calling AI agent (NVIDIA NIM API, function calling over medicines/prescriptions, and voice message transcription via NVIDIA Riva ASR). The AI agent was removed from `main` after real usage data showed users weren't using it; `legacy-ai` is kept around purely as a reference in case that functionality is ever revisited. It is not deployed and is not actively maintained.

---

## Features

- **Medication Management** — add, edit (name, dosage, intake time, course length, stock, low-stock threshold), extend, archive, and delete medication schedules with timezone-aware reminders and stock tracking (low-stock alerts, restock flow)
- **Prescription Management** — track prescriptions with expiration dates, allowed quantities, partial-purchase tracking, expiry reminders, and auto-archiving of expired prescriptions
- **Smart, Persistent Reminders** — APScheduler-based notifications with hourly follow-ups for unacknowledged intakes, a per-user repeat-reminders on/off toggle, and misfire grace handling; pending reminders survive container restarts via Redis, preserving the original hourly cadence instead of resetting it
- **Geo-Based Timezone Setup** — users type their city and country and the bot resolves the IANA timezone automatically (geopy + timezonefinder), rescheduling all reminders instantly
- **Reports & Export** — styled Excel (.xlsx) and CSV reports of medication history, including per-medicine adherence statistics
- **Automated Database Backups** — daily `pg_dump` with gzip compression, configurable retention, and optional offsite upload to any S3-compatible storage (e.g. Oracle Object Storage)
- **Admin Panel** — FastAPI + SQLAdmin dashboard with adherence charts, a live reminder-queue view (pending/unacknowledged reminders with auto-refresh), medicine/prescription/user management (including bot-blocked and active/total user tracking), and a built-in log viewer
- **Event-Driven Sync** — real-time scheduler updates via an internal webhook whenever the admin panel changes data
- **Data Encryption** — sensitive user data (medicine names, prescription names) encrypted at rest using the `cryptography` library
- **Multilingual** — Ukrainian, English, and Russian interface
- **Security Hardening** — webhook port restricted to Telegram's IP ranges, Basic Auth on the admin panel, SSH/admin access restricted by IP
- **CI/CD Pipeline** — GitHub Actions runs lint (ruff), type checks (mypy), and the pytest suite (92%+ coverage) on every push/PR; a self-hosted-runner deploy workflow then builds and ships to production, polls container health, auto-rolls back to the previous commit on a failed health check, and posts deploy success/failure alerts to Telegram

---

## Tech Stack

| Category | Technologies |
|---|---|
| Language | Python 3.14 |
| Bot Framework | aiogram 3 (async) |
| Web Framework | FastAPI, uvicorn |
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
│   ├── dashboard.py              # Charts page
│   ├── logs_viewer.py            # Live log viewer endpoint
│   ├── model_views.py            # ModelView definitions (User, Medicine, Prescription, ...)
│   └── sync.py                   # Notifies the bot process of admin-made changes
│
├── web/
│   └── internal_api.py          # Internal webhook endpoints (admin → bot sync, health check)
│
├── handlers/                   # Telegram command and message handlers
│   ├── start.py                  # /start, /help, language selection
│   ├── medicines/                # add, edit, extend, archive, intake, restock, listing, menu, keyboards (FSM-based)
│   ├── prescriptions/            # add, edit, buy, archive, restore, stock, listing, menu, keyboards
│   ├── report.py                 # Excel/CSV report generation
│   ├── settings.py               # Name, timezone, language, repeat-reminders toggle, feedback
│   ├── bot_status.py             # Bot-blocked detection
│   ├── common.py                 # Shared user/language resolution helpers
│   └── errors.py                 # Global exception handler
│
├── services/
│   ├── scheduler/                # jobs/ (core, reminders, sync), prescriptions.py, redis_state.py
│   ├── report_service/           # aggregation, excel, csv
│   ├── backup_service.py         # Daily pg_dump + offsite (S3-compatible) upload
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
│   └── _reports.py                # Report strings
│
├── templates/sqladmin/          # Custom Jinja2 templates (index, logs, reminders, login, layout)
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
# Fill in BOT_TOKEN, DB credentials, and (optionally) backup settings

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
| Reminder received | Wait for scheduled time | Message with ✅ Taken / ⏭️ Skip buttons; hourly follow-ups until acknowledged (if enabled), even across bot restarts |
| Mark prescription purchased | 📝 Prescriptions → Mark bought | Purchased quantity updated, optional stock top-up for the linked medicine |
| Admin change | Edit via web panel | Bot scheduler syncs instantly via internal webhook |
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
| aiohttp | Async HTTP requests to internal webhooks |
| pytz / zoneinfo | Timezone handling and validation |
| geopy | Geocoding user-entered city/country to coordinates |
| timezonefinder | Coordinates → IANA timezone lookup |
| fastapi + uvicorn | Admin panel and internal webhooks |
| sqladmin | Web-based admin dashboard |
| cryptography | Encryption of sensitive user data |
| redis | FSM state storage and persistent reminder state |
| boto3 | Offsite database backup uploads (S3-compatible storage) |
| itsdangerous | Signed session cookies for the admin panel |

</details>
