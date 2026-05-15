# FXY Study System

AI-powered study assistant for university exam preparation, built on Obsidian + self-hosted services.

## Architecture

```
Local Obsidian Vault ←→ WebDAV Sync ←→ Server
                                        ├── Node.js API (17 endpoints)
                                        ├── Python Automation (12 cron modes)
                                        ├── 11 Web Apps (PWA)
                                        └── Docker Services (Vikunja/Memos/Glance/...)
```

## Features

### AI Agents
- **Smart Note**: Save Gemini conversations → auto-extract flashcards + review essence + weakness detection + problem extraction (with Vision OCR)
- **Daily Challenge**: Push 3 quiz questions to Memos every morning, AI grades answers at night
- **Weakness Analysis**: Cross-source analysis (notes + flashcards + spaced repetition + task completion)
- **Coverage Check**: Match course chapters against study records, flag uncovered topics

### Automation
- **Task Auto-Rebalance**: When daily tasks > 4, auto-postpone low-priority items
- **Canvas DDL Sync**: Auto-create Vikunja tasks from Canvas assignments
- **Study Log**: `#学了 高数 45min` in Memos → auto-track study time
- **Quick Lookup**: `#查 格林公式` → instant search results (no AI, 3 seconds)
- **Bedtime Review**: Push top 3 knowledge points at 10 PM

### Reports
- Morning report (tasks + weather + GPA countdown + AI study advice)
- Evening report (completion stats + study time + weekly comparison)
- Learning review (knowledge mastery tracking ✅⚠️❌)
- Weekly report (weakness tracking)

### Web Apps
- **Command Center** (index.html): Live stats dashboard
- **AI Problem Bank** (problems.html): Auto-extracted problems with review mode
- **Weakness Analysis** (weakness.html): Per-subject analysis + one-click study plan
- **Flashcards** (flashcards.html): 250+ cards, spaced repetition
- **Planner** (planner.html): AI-adaptive daily plan
- And more: chat, formulas, dashboard, focus timer, task agent

### Browser Extension
- Gemini to Obsidian: One-click save AI conversations with image support

## Tech Stack
- **Backend**: Node.js (raw http) + Python 3
- **AI**: DeepSeek V3.2 / Qwen (Vision) via OpenAI-compatible API
- **Task Management**: Vikunja (self-hosted)
- **Quick Notes**: Memos (self-hosted)
- **Push Notifications**: Bark (iOS)
- **Static Site**: Quartz (Obsidian → website)
- **Reverse Proxy**: Nginx + Cloudflare Tunnel
- **Note Sync**: WebDAV (Remotely Save plugin)

## Setup

1. Copy `server/config.example.env` to `server/.env` and fill in credentials
2. Deploy `server/` files to your server
3. Set up systemd service (`scripts/chat-server.service`)
4. Configure nginx (`scripts/nginx-notes.conf`)
5. Install crontab (`scripts/crontab.conf`)
6. Deploy `frontend/apps/` to your web root
7. Load `extension/` as unpacked Chrome/Edge extension

## File Structure

```
server/
  chat-server.cjs       # Node.js API server (17 endpoints)
  vikunja-dashboard.py   # Python automation (12 modes)
  news_fetcher.py        # Academic news aggregator
  vikunja-reminder.py    # Task deadline push notifications
  config.example.env     # Credential template

frontend/apps/
  index.html             # Command center dashboard
  problems.html          # AI problem bank
  weakness.html          # Weakness analysis
  flashcards.html        # Spaced repetition
  planner.html           # AI daily planner
  chat.html              # RAG-powered chat
  dashboard.html         # Study heatmap + GPA sim
  formulas.html          # Formula quick reference
  mistakes.html          # Error notebook
  focus.html             # Pomodoro timer
  task-agent.html        # Natural language task creation

extension/
  manifest.json          # Chrome MV3 extension
  content.js             # Gemini page content extraction
  popup.html/js          # Extension popup UI

scripts/
  quartz-build.sh        # Auto-build Quartz site
  crontab.conf           # Full cron schedule
  nginx-notes.conf       # Nginx site config
  chat-server.service    # Systemd unit file
```

## License
MIT
