# MedBot – AI-Powered Medication Reminder Telegram Bot

A production-ready Telegram bot for medication schedule management, built with Python and aiogram 3. Features an integrated AI assistant powered by NVIDIA NIM (Llama-3.1, Llama-3.2-Vision) with automatic fallback to a local Ollama model. Deployed on Oracle Cloud with webhook architecture, Docker Compose orchestration, and a web-based admin panel.

> Bachelor's Diploma Project · NTU "Kharkiv Polytechnic Institute" · 2026

## Try it out

[t.me/tabletkybot](https://t.me/tabletkybot)

![QR Code](qr.jpg)

---

## Features

- **Medication Management** — add, edit, and delete medication schedules with time-zone-aware reminders
- **Smart Reminders** — precise APScheduler-based notifications with hourly follow-ups for unacknowledged intakes
- **AI Assistant** — analyze medication photos, PDF instructions, and answer medical questions via chat
- **Vision Support** — automatic PDF-to-image conversion (PyMuPDF) for AI analysis
- **Dual LLM Pipeline** — NVIDIA NIM API as primary, Ollama as local fallback (no external dependencies required)
- **Reports & Export** — styled Excel (.xlsx) and CSV reports of medication history
- **Admin Panel** — FastAPI + SQLAdmin dashboard for user and medication management
- **Event-Driven Sync** — real-time scheduler updates via internal webhooks when admin changes occur
- **Data Encryption** — sensitive user data encrypted at rest using the `cryptography` library
- **Multilingual** — Ukrainian and English interface support

---

## Tech Stack

| Category | Technologies |
|---|---|
| Language | Python 3.11+ |
| Bot Framework | aiogram 3 (async) |
| Web Framework | FastAPI, uvicorn |
| AI / LLM | NVIDIA NIM API (Llama-3.1, Llama-3.2-Vision), Ollama |
| ORM | SQLAlchemy (async) |
| Database | PostgreSQL (asyncpg driver) |
| Cache / FSM | Redis |
| Scheduling | APScheduler |
| Admin Panel | SQLAdmin (Tabler UI) |
| Reports | openpyxl |
| PDF Processing | PyMuPDF (fitz) |
| DevOps | Docker, Docker Compose, Oracle Cloud |
| Security | cryptography, SSL/TLS, iptables |

---

## Project Structure

```
tgbot/
├── main.py                  # Bot entry point, aiohttp internal server
├── admin_app.py             # Admin panel entry point (FastAPI)
├── config.py                # Configuration loader (.env)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
│
├── admin/
│   └── app.py               # SQLAdmin panel setup
│
├── handlers/                # Telegram command and message handlers
│   ├── start.py             # /start, /help, language selection
│   ├── medicines.py         # Medication CRUD (FSM-based)
│   ├── ai_chat.py           # AI chat mode, PDF/photo processing
│   ├── report.py            # Excel report generation
│   ├── settings.py          # User profile (name, timezone)
│   └── errors.py            # Global exception handler
│
├── services/
│   ├── ai_service.py        # NVIDIA NIM + Ollama integration
│   ├── scheduler.py         # APScheduler (reminders + sync)
│   └── report_service.py    # Excel formatting and export
│
├── database/
│   ├── models.py            # SQLAlchemy models (Users, Medicines, Records, ChatHistory)
│   ├── db.py                # Async engine and session factory
│   └── crud.py              # Database operations
│
├── templates/sqladmin/      # Custom Jinja2 templates for admin panel
├── locales/texts.py         # UK/EN localization strings
└── middleware/
    └── db_middleware.py     # DB session injection middleware
```

---

## Getting Started

### Option 1 — Docker Compose (Recommended)

**Requirements:** Docker + Docker Compose

```bash
# 1. Configure environment
cp .env.example .env
# Fill in BOT_TOKEN, NVIDIA_API_KEY, and DB credentials

# 2. Start containers
docker compose up -d

# 3. Check logs
docker compose logs -f bot

# 4. Stop
docker compose down
```

### Option 2 — Local Setup

**Requirements:** Python 3.11+, PostgreSQL 15+

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create database
psql -U postgres
CREATE USER botuser WITH PASSWORD 'botpassword';
CREATE DATABASE medbot OWNER botuser;
\q

# 3. Configure .env and run
python main.py
```

---

## Configuration

### Telegram Bot Token
Get from [@BotFather](https://t.me/BotFather) → `/newbot`

### NVIDIA NIM API (Primary AI)
Register at [build.nvidia.com](https://build.nvidia.com), create an API key, and set in `.env`:

```env
NVIDIA_API_KEY=nvapi-...
NVIDIA_MODEL=meta/llama-3.1-70b-instruct
NVIDIA_VISION_MODEL=meta-llama/llama-3.2-11b-vision-instruct
```

### Ollama (Local Fallback AI)
Install [Ollama](https://ollama.com), pull models, and configure:

```bash
ollama pull llama3
ollama pull llava
```

```env
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3
OLLAMA_VISION_MODEL=llava
```

---

## Key User Scenarios

| Scenario | Action | Result |
|---|---|---|
| Registration | `/start` → Settings ⚙️ | Language selection, name and timezone setup |
| Add medication | 💊 Medicines → ➕ Add | Time validation, scheduler starts |
| Edit schedule | 💊 Medicines → ✏️ Edit | Real-time reminder rescheduling |
| Admin change | Edit via web panel | Bot scheduler syncs instantly via internal webhook |
| Reminder received | Wait for scheduled time | Message with ✅ Taken / ⏭️ Skip buttons |
| AI photo analysis | 🤖 AI Mode → Send photo/PDF | PDF converted to image, AI analyzes instructions |
| Export data | 📤 Reports | Styled `.xlsx` / `.csv` with full medication history |

---

## Dependencies

| Library | Purpose |
|---|---|
| aiogram | Async Telegram Bot API framework |
| SQLAlchemy + asyncpg | Async ORM and PostgreSQL driver |
| APScheduler | Background task scheduling |
| openpyxl | Excel report generation |
| aiohttp | Async HTTP requests to AI APIs |
| PyMuPDF (fitz) | PDF to image conversion for AI Vision |
| pytz | Timezone handling and validation |
| fastapi + uvicorn | Admin panel and internal webhooks |
| sqladmin | Web-based admin dashboard |
| cryptography | Encryption of sensitive user data |
| redis | FSM state storage |
