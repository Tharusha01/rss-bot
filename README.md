# 📰 Telegram RSS News Bot

A production-ready Telegram bot that automatically monitors RSS feeds, extracts full article content, and posts formatted summaries to your Telegram channels — with native Sinhala Unicode support.

---

## ✨ Features

| Feature | Details |
|---------|---------|
| 🔔 RSS Monitoring | Polls feeds every 5 minutes (configurable) |
| 🌐 Multi-Feed | Unlimited feeds stored in SQLite |
| 📝 Article Extraction | newspaper3k → BeautifulSoup → RSS fallback chain |
| 🇱🇰 Sinhala Unicode | Full UTF-8 support in extraction, formatting, and logging |
| 🔁 Deduplication | SQLite-backed; never re-posts the same article |
| 🖼️ Images | Auto-detects og:image / media:content thumbnails |
| ⏱️ Rate Limiting | Configurable delay between messages + flood control handling |
| 🛡️ Admin-Only | Feed management commands restricted to whitelisted user IDs |
| 🔄 Auto Retry | Exponential back-off on network failures |
| 🧹 Auto Cleanup | Removes sent-article records older than N days |
| 🐳 Docker Ready | Multi-stage Dockerfile + docker-compose |
| 🚀 Deploy-Ready | Railway / Koyeb / VPS compatible |

---

## 🗂️ Project Structure

```
rss-bot/
├── main.py                  # Entry point
├── src/
│   ├── __init__.py
│   ├── config.py            # Environment config + validation
│   ├── logger.py            # Rotating file + console logging
│   ├── database.py          # SQLite layer (feeds + dedup)
│   ├── feed_parser.py       # RSS fetch + article item extraction
│   ├── article_extractor.py # Full article content + image extraction
│   ├── telegram_sender.py   # Formatted Telegram message sending
│   ├── scheduler.py         # APScheduler setup
│   ├── poller.py            # Core polling/dispatch logic
│   └── handlers.py          # Telegram command handlers
├── tests/
│   ├── test_database.py
│   └── test_feed_parser.py
├── data/                    # SQLite DB (auto-created, git-ignored)
├── logs/                    # Log files (auto-created, git-ignored)
├── .env.example             # Copy → .env and fill in values
├── requirements.txt
├── pyproject.toml           # Pytest config
├── Dockerfile
├── docker-compose.yml
└── .dockerignore
```

---

## 🚀 Quick Start

### 1 — Prerequisites

- Python 3.12+
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- A channel/group where the bot is an **admin with post permissions**

### 2 — Install

```bash
git clone https://github.com/your-org/rss-bot.git
cd rss-bot

python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### 3 — Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
BOT_TOKEN=7123456789:AAF...your_token
CHANNEL_ID=@your_channel      # or -1001234567890
ADMIN_IDS=123456789           # your Telegram user ID
```

> **How to find your Telegram user ID?** Message [@userinfobot](https://t.me/userinfobot).

### 4 — Run

```bash
python main.py
```

---

## 📟 Telegram Commands

| Command | Access | Description |
|---------|--------|-------------|
| `/start` | Everyone | Show help and status |
| `/addfeed <url>` | Admin | Add an RSS feed |
| `/removefeed <url>` | Admin | Remove (deactivate) a feed |
| `/listfeeds` | Admin | List all active feeds |
| `/testfeed <url>` | Admin | Fetch & preview the latest article |
| `/latest` | Admin | Trigger an immediate poll |

---

## 📬 Message Format

```
📰 Article Title Here

Short article summary of up to 300 characters drawn from the
full article content...

🔗 Read More — Source Name
```

---

## 🐳 Docker Deployment

```bash
# Build and start
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

Data and logs are persisted via bind mounts to `./data` and `./logs`.

---

## ☁️ Cloud Deployment

### Railway

1. Push the repo to GitHub.
2. Create a new Railway project → **Deploy from GitHub repo**.
3. Add all `.env` variables under **Variables**.
4. Railway auto-detects the `Dockerfile` and builds/deploys.

### Koyeb

1. Create a new Koyeb app → **Docker image**.
2. Point to your registry or use GitHub Actions to push the image.
3. Set environment variables in the Koyeb dashboard.

### VPS (systemd)

```ini
# /etc/systemd/system/rss-bot.service
[Unit]
Description=Telegram RSS News Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/rss-bot
EnvironmentFile=/opt/rss-bot/.env
ExecStart=/opt/rss-bot/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now rss-bot
```

---

## 🗃️ Database Schema

```sql
-- Active RSS feeds
CREATE TABLE feeds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT    NOT NULL UNIQUE,
    added_by    INTEGER NOT NULL,   -- Telegram user_id
    channel_id  TEXT    NOT NULL,   -- Target channel
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL
);

-- Sent articles (deduplication)
CREATE TABLE sent_articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id     INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    article_url TEXT    NOT NULL,
    sent_at     TEXT    NOT NULL,
    UNIQUE (feed_id, article_url)
);
```

---

## 🧪 Testing

```bash
pip install pytest
pytest
```

---

## ⚙️ Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_TOKEN` | — | **Required** — Telegram bot token |
| `CHANNEL_ID` | — | **Required** — Target channel/group |
| `ADMIN_IDS` | — | Comma-separated admin user IDs |
| `FETCH_INTERVAL_MINUTES` | `5` | Feed polling interval |
| `DATABASE_PATH` | `data/rss_bot.db` | SQLite file path |
| `MAX_SUMMARY_LENGTH` | `300` | Article summary character limit |
| `REQUEST_TIMEOUT` | `15` | HTTP timeout (seconds) |
| `MAX_RETRIES` | `3` | Network retry attempts |
| `RETRY_DELAY_SECONDS` | `5` | Base retry delay |
| `MESSAGE_DELAY_SECONDS` | `1.5` | Delay between Telegram messages |
| `MAX_MESSAGES_PER_FEED` | `5` | Max new posts per feed per poll |
| `DB_CLEANUP_DAYS` | `30` | Days before pruning sent records |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FILE` | `logs/rss_bot.log` | Log file path (blank = disable) |

---

## 🤝 Contributing

Pull requests are welcome! Please:
- Follow [PEP 8](https://pep8.org/) style.
- Add tests for new functionality.
- Keep commits atomic and messages descriptive.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
