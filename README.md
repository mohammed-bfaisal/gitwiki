# 🐙 GitWiki

> Browse GitHub like Wikipedia — a self-hosted, lazy-loading encyclopedia of public repositories.

![Python](https://img.shields.io/badge/Python-3.10+-3572A5?style=flat&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0+-000000?style=flat&logo=flask&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat)
![GitHub API](https://img.shields.io/badge/GitHub%20API-v2022--11--28-181717?style=flat&logo=github)

GitWiki is a local web app that turns GitHub's API into a Wikipedia-style browsing experience. Search and explore public repos by topic, language, and activity — with full README rendering, language breakdowns, file trees, and contributor info — all fetched on demand as you browse, never upfront.

---

## ✨ Features

- **Wikipedia-style UI** — serif fonts, infoboxes, table of contents, article layout
- **Lazy loading** — data fetches only when you click. No bulk downloads, no waiting
- **Topic browsing** — AI/ML, Security, DevTools, and Trending categories
- **Language filtering** — filter any topic by Python, JavaScript, or TypeScript
- **Full repo pages** — rendered README, language bar, file tree, contributors, releases
- **Local cache** — results cached as JSON so re-browsing costs zero API calls
- **Download database** — export everything you've browsed to JSON, CSV, or SQLite (with FTS5 full-text search)
- **Dark mode** — persists across sessions, auto-detects your OS preference
- **Rate limit display** — always know how many API requests you have left

---

## 🚀 Quick Start

### 1. Clone & install
```bash
git clone https://github.com/mohammed-bfaisal/gitwiki.git
cd gitwiki
pip install -r requirements.txt
```

### 2. Get a GitHub token *(optional but recommended)*

Without a token you get **60 requests/hour**. With one: **5,000/hour**.

1. Go to [github.com/settings/tokens](https://github.com/settings/tokens)
2. Click **Generate new token (classic)**
3. Give it a name — **leave every checkbox unchecked** (no scopes needed)
4. Copy the `ghp_...` string
```bash
export GITHUB_TOKEN=ghp_yourtoken
```

> **Never put your token in the code.** GitWiki reads it from the environment variable only.

### 3. Run
```bash
python server.py
```

Open **http://localhost:5000**

---

## 🗂️ Project Structure
```
gitwiki/
├── server.py        ← Flask backend: GitHub API proxy, caching, DB export
├── wiki.html        ← Frontend: Wikipedia-style UI (single file, no build step)
├── requirements.txt
├── .gitignore
└── cache/           ← Auto-created. JSON cache files (gitignored)
```

---

## ⚙️ Configuration

Everything is in the `CONFIG` block at the top of `server.py`:
```python
CONFIG = {
    "github_token":      os.getenv("GITHUB_TOKEN", ""),  # never hardcode
    "min_stars":         0,       # 0 = all, 100 = quality filter
    "repos_per_page":    30,
    "cache_ttl_minutes": 60,
    "topics": [...],    # add/remove topic categories
    "languages": [...], # add/remove language filters
}
```

### Adding a custom topic
```python
{
    "id":    "robotics",
    "label": "Robotics & Embedded",
    "icon":  "🤖",
    "query": "topic:robotics",
    "sort":  "updated",
},
```

---

## 💾 Download Database

Click **⬇ Download DB** in the topbar to export what you've browsed:

| Format | Best for |
|--------|----------|
| **JSON** | Scripts, APIs, further processing |
| **CSV** | Excel / Google Sheets |
| **SQLite** | SQL queries with full-text search (FTS5) |
```sql
-- Example SQLite query
SELECT name, stars, language FROM repos
WHERE repos MATCH 'transformer llm'
ORDER BY stars DESC;
```

---

## 🔑 Rate Limits

| Mode | Requests/hour |
|------|---------------|
| No token | 60 |
| With token (no scopes needed) | 5,000 |

Cached pages cost **zero** requests.

---

## 🛠️ Stack

**Backend** — Python 3.10+, Flask, Requests, Markdown2  
**Frontend** — Vanilla HTML/CSS/JS (no framework, no build step)  
**Fonts** — Linux Libertine, Noto Sans, Source Code Pro  
**API** — GitHub REST API v2022-11-28

---

## 📄 License

MIT

---

<p align="center">Built by <a href="https://github.com/mohammed-bfaisal">mohammed-bfaisal</a></p>
