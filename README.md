# 🏆 Tournament OS 2.0

A production-grade **Discord Tournament Bot** for running esports tournaments.
Multi-org, AI-assisted, and Railway-ready.

---

## ✨ Features

| Surface | What it does |
|---------|-------------|
| `/setup tournament` | 7-step wizard — creates roles, categories & 20 channels automatically |
| `#register` | Persistent button — players click to register; no commands needed |
| `#verification-queue` | Auto-posted Approve / Reject / Hold / Flag / Send Back cards |
| `#create-tournament` | Persistent button — 6-step tournament creation wizard |
| **Control Panel** | 9 action buttons per tournament (Status, Bracket, Check-in, Matches, etc.) |
| **Support Tickets** | `#support` button → private thread per user |
| `/ask` | AI assistant (Groq `llama-3.3-70b-versatile`) scoped per guild/tournament |

---

## 🚀 Deploy to Railway

### Step 1 — Create project
1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → select `tournament-os`
2. Add a **PostgreSQL** database plugin

### Step 2 — Set environment variables

In the service **Variables** tab:

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | click **Add Reference** → PostgreSQL plugin (auto-filled) |
| `DISCORD_TOKEN` | your Discord bot token |
| `DISCORD_CLIENT_ID` | your Discord application client ID |
| `GROQ_API_KEY` | from [console.groq.com](https://console.groq.com) (free) |
| `ENVIRONMENT` | `production` |

### Step 3 — Deploy
Railway uses `railway.toml` automatically. It will:
- Build with `Dockerfile.bot`
- Run `alembic upgrade head` (migrations) then `python bot_main.py`

> No healthcheck — the bot is a background process, not a web server.

---

## 🖥️ Local Development

```bash
git clone https://github.com/officialtomein-pixel/tournament-os.git
cd tournament-os

pip install -r requirements.txt
cp .env.example .env   # fill in your values

alembic upgrade head
python bot_main.py
```

Or with Docker Compose (includes PostgreSQL):
```bash
docker compose up -d
```

---

## 🎮 Admin Workflow

### Phase 1 — First-Time Setup (once per server)
Run `/setup tournament` → 7-step wizard creates all roles and channels automatically.

### Phase 2 — Create a Tournament
Click **Create Tournament** in `#create-tournament` → 6-step wizard.

### Phase 3 — Manage via Control Panel
Every tournament gets a **Control Panel** with 9 buttons — no commands needed.

### Phase 4 — Review Registrations
Cards appear in `#verification-queue` with Approve / Reject / Hold / Flag / Send Back buttons.

### Phase 5 — Run Matches
```
/tournament_generate_bracket [tournament_id]   ← generate bracket (staff)
/submit_score [match_id]                       ← players submit scores
/score_override [match_id]                     ← staff override (Referee+)
```

---

## 📋 All Slash Commands

| Command | Permission | Description |
|---------|-----------|-------------|
| `/setup tournament` | Owner | One-time server setup wizard |
| `/tournament_generate_bracket` | Admin | Generate bracket |
| `/analytics` | Admin | View tournament stats |
| `/score_override` | Referee+ | Override a match score |
| `/dispute_list` | Moderator+ | List open disputes |
| `/dispute_assign` | Moderator+ | Assign dispute to staff |
| `/dispute_resolve` | Moderator+ | Close a dispute |
| `/my_registration` | Anyone | Check your registration status |
| `/submit_score` | Players | Submit match result |
| `/standings` | Anyone | View standings |
| `/ask` | Anyone | AI assistant |

---

## 🏗️ Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11 |
| Discord | discord.py 2.x |
| Database | PostgreSQL 16 + SQLAlchemy 2.0 async (asyncpg) |
| Migrations | Alembic (auto-runs on deploy) |
| AI | Groq API — `llama-3.3-70b-versatile` |
| Deployment | Railway / Docker |

---

## 📁 Project Structure

```
tournament-os/
├── bot_main.py                   Discord bot entrypoint
├── requirements.txt
├── alembic.ini
├── Dockerfile.bot
├── railway.toml                  Railway deploy config
├── docker-compose.yml            Local dev (bot + PostgreSQL)
└── app/
    ├── bot/
    │   ├── cogs/                 Slash commands (admin, match, registration, dispute, AI)
    │   ├── views/                All Discord UI (buttons, modals, wizards, control panel)
    │   └── helpers/              Formatters, permissions
    ├── services/                 Business logic (bracket, scoring, registration, disputes)
    ├── database/
    │   ├── models/               17 SQLAlchemy ORM models
    │   ├── repositories/         Typed repos with org isolation
    │   └── migrations/           Alembic DDL migrations
    ├── ai/                       Groq AI assistant
    └── config/settings.py        Env-var config (pydantic-settings)
```

---

## 📄 License

MIT
