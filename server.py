#!/usr/bin/env python3
"""
GitHub Wiki Server
──────────────────
A lazy-loading local Wikipedia for GitHub.
Fetches data on demand as you browse — never downloads everything upfront.

Usage:
    pip install flask requests markdown2
    python server.py

Then open: http://localhost:5000
Optional: set GITHUB_TOKEN env var for 5000 req/hr instead of 60 req/hr
    export GITHUB_TOKEN=ghp_yourtoken
"""

import os
import re
import json
import base64
import time
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps
import requests
from flask import Flask, jsonify, send_file, request, Response
import markdown2

# ══════════════════════════════════════════════════════════════════
#  CONFIG — Edit freely. Everything here is tunable.
# ══════════════════════════════════════════════════════════════════

CONFIG = {
    # Your GitHub token — or set GITHUB_TOKEN env var
    # Get one at: https://github.com/settings/tokens (no scopes needed for public data)
    "github_token": os.getenv("GITHUB_TOKEN", ""),

    # Minimum stars to show a repo.
    # 0 = show everything (sorted by recent activity / trending)
    # 50 = broad, 500 = quality, 1000 = popular only
    "min_stars": 0,

    # How many repos to load per page/category
    "repos_per_page": 30,

    # Cache TTL in minutes (avoids re-hitting API on refresh)
    "cache_ttl_minutes": 60,

    # Cache directory (stores JSON blobs locally so browsing is fast)
    "cache_dir": "./cache",

    # ── Topics ─────────────────────────────────────────────────────
    # Each entry: { "label": display name, "query": GitHub search query }
    # GitHub search syntax: https://docs.github.com/en/search-github/searching-on-github
    "topics": [
        {
            "id": "ai-ml",
            "label": "AI & Machine Learning",
            "icon": "🤖",
            "description": "LLMs, agents, neural networks, ML frameworks, fine-tuning, RAG, and AI tooling.",
            # Single topic: qualifier — always works, authenticated or not
            "query": "topic:machine-learning",
            "sort": "updated",
        },
        {
            "id": "security",
            "label": "Security & Hacking",
            "icon": "🔐",
            "description": "CTF tools, penetration testing, vulnerability research, OSINT, exploit dev, and defensive security.",
            "query": "topic:security",
            "sort": "updated",
        },
        {
            "id": "devtools",
            "label": "DevTools & CLI",
            "icon": "🛠️",
            "description": "Terminal tools, shells, editors, build systems, automation, and developer productivity software.",
            "query": "topic:cli",
            "sort": "updated",
        },
        {
            "id": "trending",
            "label": "Trending Now",
            "icon": "🔥",
            "description": "The most recently pushed repos across all topics, sorted by activity.",
            "query": "stars:>10",
            "sort": "updated",
        },
    ],

    # ── Languages ──────────────────────────────────────────────────
    "languages": [
        {"id": "python",     "label": "Python",     "icon": "🐍", "color": "#3572A5"},
        {"id": "javascript", "label": "JavaScript",  "icon": "⚡", "color": "#F1E05A"},
        {"id": "typescript", "label": "TypeScript",  "icon": "📘", "color": "#3178C6"},
    ],
}

# ══════════════════════════════════════════════════════════════════

app = Flask(__name__)
CACHE_DIR = Path(CONFIG["cache_dir"])
CACHE_DIR.mkdir(exist_ok=True)

GH_BASE = "https://api.github.com"


# ─── Helpers ───────────────────────────────────────────────────────────────────

def gh_headers() -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = CONFIG["github_token"]
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def cache_key(key: str) -> Path:
    safe = hashlib.md5(key.encode()).hexdigest()
    return CACHE_DIR / f"{safe}.json"


def cache_get(key: str):
    path = cache_key(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        expires = datetime.fromisoformat(data["expires"])
        if datetime.utcnow() > expires:
            path.unlink(missing_ok=True)
            return None
        return data["payload"]
    except Exception:
        return None


def cache_set(key: str, payload, ttl_minutes: int = None):
    ttl = ttl_minutes or CONFIG["cache_ttl_minutes"]
    path = cache_key(key)
    data = {
        "expires": (datetime.utcnow() + timedelta(minutes=ttl)).isoformat(),
        "payload": payload,
    }
    path.write_text(json.dumps(data, ensure_ascii=False))


def gh_get(url: str, params: dict = None):
    """Rate-limit-aware GitHub GET with exponential backoff."""
    headers = gh_headers()
    for attempt in range(4):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
        except requests.RequestException as e:
            time.sleep(2 ** attempt)
            continue

        if r.status_code == 403:
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 30))
            wait = max(reset - int(time.time()), 5)
            return {"_rate_limited": True, "retry_after": wait}

        if r.status_code == 422:
            # Invalid query — surface the error message so we can debug
            try:
                body = r.json()
                msg = body.get("message", "Invalid query")
            except Exception:
                msg = r.text[:200]
            return {"_query_error": True, "message": msg, "query": str(params)}

        if r.status_code == 404:
            return None

        if r.status_code >= 400:
            return {"_http_error": True, "status": r.status_code, "message": r.text[:200]}

        return r.json()

    return None


def search_repos(query: str, language: str = None, page: int = 1,
                 sort: str = "stars", min_stars: int = None) -> dict:
    """Search GitHub repos with filters."""
    min_s = min_stars if min_stars is not None else CONFIG["min_stars"]

    parts = [query.strip()]
    if min_s and min_s > 0:
        parts.append(f"stars:>{min_s}")
    parts.append("is:public")
    if language:
        parts.append(f"language:{language}")

    full_query = " ".join(parts)

    # Log query for debugging
    print(f"[search] q={full_query!r} sort={sort} page={page}")

    params = {
        "q": full_query,
        "sort": sort,
        "order": "desc",
        "per_page": CONFIG["repos_per_page"],
        "page": page,
    }
    result = gh_get(f"{GH_BASE}/search/repositories", params)
    if result is None:
        return {}
    return result


@app.route("/api/debug/query")
def api_debug_query():
    """Test a raw query against GitHub search — shows exactly what GitHub returns."""
    q = request.args.get("q", "topic:llm is:public")
    print(f"[debug] testing query: {q!r}")
    result = gh_get(f"{GH_BASE}/search/repositories", {
        "q": q, "sort": "updated", "order": "desc", "per_page": 5, "page": 1
    })
    return jsonify({
        "query": q,
        "result_type": type(result).__name__,
        "raw": result,
    })


def fetch_readme(owner: str, repo: str) -> str:
    """Fetch and decode README, return as rendered HTML."""
    data = gh_get(f"{GH_BASE}/repos/{owner}/{repo}/readme")
    if not data or "content" not in data:
        return ""
    try:
        raw = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        # Convert markdown to HTML
        html = markdown2.markdown(
            raw,
            extras=[
                "fenced-code-blocks",
                "tables",
                "header-ids",
                "strike",
                "task_list",
                "code-friendly",
                "footnotes",
            ],
        )
        return html
    except Exception:
        return ""


def fetch_languages(owner: str, repo: str) -> dict:
    return gh_get(f"{GH_BASE}/repos/{owner}/{repo}/languages") or {}


def fetch_file_tree(owner: str, repo: str, branch: str) -> list:
    data = gh_get(f"{GH_BASE}/repos/{owner}/{repo}/git/trees/{branch}?recursive=0")
    if not data or "tree" not in data:
        return []
    items = [i for i in data["tree"] if i["type"] in ("blob", "tree")]
    # Organize into tree structure
    files = []
    dirs = []
    for item in items[:80]:
        entry = {"path": item["path"], "type": item["type"], "size": item.get("size", 0)}
        if item["type"] == "tree":
            dirs.append(entry)
        else:
            files.append(entry)
    return dirs + files


def fetch_contributors(owner: str, repo: str) -> list:
    data = gh_get(f"{GH_BASE}/repos/{owner}/{repo}/contributors", {"per_page": 5})
    if not isinstance(data, list):
        return []
    return [
        {"login": c["login"], "contributions": c["contributions"], "avatar": c["avatar_url"]}
        for c in data[:5]
    ]


def fetch_releases(owner: str, repo: str) -> list:
    data = gh_get(f"{GH_BASE}/repos/{owner}/{repo}/releases", {"per_page": 3})
    if not isinstance(data, list):
        return []
    return [
        {
            "tag": r["tag_name"],
            "name": r["name"] or r["tag_name"],
            "date": r["published_at"],
            "url": r["html_url"],
        }
        for r in data[:3]
    ]


def serialize_repo(r: dict) -> dict:
    """Flatten a GitHub repo API response to what the frontend needs."""
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "full_name": r.get("full_name"),
        "owner": r.get("owner", {}).get("login"),
        "owner_avatar": r.get("owner", {}).get("avatar_url"),
        "description": r.get("description") or "",
        "url": r.get("html_url"),
        "stars": r.get("stargazers_count", 0),
        "forks": r.get("forks_count", 0),
        "watchers": r.get("watchers_count", 0),
        "open_issues": r.get("open_issues_count", 0),
        "language": r.get("language") or "Unknown",
        "topics": r.get("topics", []),
        "license": (r.get("license") or {}).get("spdx_id") or "No license",
        "is_fork": r.get("fork", False),
        "is_archived": r.get("archived", False),
        "default_branch": r.get("default_branch", "main"),
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
        "pushed_at": r.get("pushed_at"),
        "size_kb": r.get("size", 0),
        "homepage": r.get("homepage") or "",
    }


# ─── API Routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file("wiki.html")


@app.route("/api/config")
def api_config():
    """Send frontend the topic/language config."""
    return jsonify({
        "topics": CONFIG["topics"],
        "languages": CONFIG["languages"],
        "min_stars": CONFIG["min_stars"],
        "authenticated": bool(CONFIG["github_token"]),
    })


@app.route("/api/rate-limit")
def api_rate_limit():
    data = gh_get(f"{GH_BASE}/rate_limit")
    if not data:
        return jsonify({"error": "Could not fetch rate limit"})
    core = data.get("resources", {}).get("search", {})
    return jsonify({
        "limit": core.get("limit"),
        "remaining": core.get("remaining"),
        "reset": core.get("reset"),
    })


@app.route("/api/topic/<topic_id>")
def api_topic(topic_id):
    page = int(request.args.get("page", 1))
    language = request.args.get("language", "")

    topic = next((t for t in CONFIG["topics"] if t["id"] == topic_id), None)
    if not topic:
        return jsonify({"error": "Topic not found"}), 404

    cache_k = f"topic:{topic_id}:lang:{language}:page:{page}"
    cached = cache_get(cache_k)
    if cached:
        return jsonify(cached)

    sort = topic.get("sort", "stars")
    result = search_repos(topic["query"], language or None, page=page, sort=sort)

    if result.get("_rate_limited"):
        return jsonify({"error": "rate_limited", "retry_after": result["retry_after"]}), 429
    if result.get("_query_error"):
        # Return as 200 so frontend error handler gets the message text
        return jsonify({"error": f"GitHub rejected this query: {result['message']}", "debug_query": result.get("query"), "repos": [], "total": 0})
    if result.get("_http_error"):
        return jsonify({"error": f"GitHub API error {result['status']}: {result['message']}", "repos": [], "total": 0})

    items = result.get("items", [])
    total = result.get("total_count", 0)

    payload = {
        "topic": topic,
        "total": total,
        "page": page,
        "repos": [serialize_repo(r) for r in items],
    }
    cache_set(cache_k, payload)
    return jsonify(payload)


@app.route("/api/language/<lang_id>")
def api_language(lang_id):
    page = int(request.args.get("page", 1))
    topic_id = request.args.get("topic", "")

    lang = next((l for l in CONFIG["languages"] if l["id"] == lang_id), None)
    if not lang:
        return jsonify({"error": "Language not found"}), 404

    base_query = "stars:>0"
    if topic_id:
        topic = next((t for t in CONFIG["topics"] if t["id"] == topic_id), None)
        if topic:
            base_query = topic["query"]

    cache_k = f"lang:{lang_id}:topic:{topic_id}:page:{page}"
    cached = cache_get(cache_k)
    if cached:
        return jsonify(cached)

    result = search_repos(base_query, lang["label"].split("/")[0].strip(), page=page)
    if result.get("_rate_limited"):
        return jsonify({"error": "rate_limited", "retry_after": result["retry_after"]}), 429

    items = result.get("items", [])
    payload = {
        "language": lang,
        "total": result.get("total_count", 0),
        "page": page,
        "repos": [serialize_repo(r) for r in items],
    }
    cache_set(cache_k, payload)
    return jsonify(payload)


@app.route("/api/repo/<owner>/<repo>")
def api_repo(owner, repo):
    """Full repo details page — fetched lazily when user opens a repo."""
    cache_k = f"repo:{owner}/{repo}"
    cached = cache_get(cache_k)
    if cached:
        return jsonify(cached)

    # Fetch everything in parallel-ish (sequential but fast enough)
    repo_data = gh_get(f"{GH_BASE}/repos/{owner}/{repo}")
    if not repo_data:
        return jsonify({"error": "Repo not found"}), 404

    base = serialize_repo(repo_data)
    branch = base["default_branch"]

    readme_html = fetch_readme(owner, repo)
    languages = fetch_languages(owner, repo)
    file_tree = fetch_file_tree(owner, repo, branch)
    contributors = fetch_contributors(owner, repo)
    releases = fetch_releases(owner, repo)

    # Language percentages
    total_bytes = sum(languages.values()) or 1
    lang_breakdown = [
        {"name": k, "bytes": v, "pct": round(v / total_bytes * 100, 1)}
        for k, v in sorted(languages.items(), key=lambda x: -x[1])
    ]

    payload = {
        **base,
        "readme_html": readme_html,
        "languages": lang_breakdown,
        "file_tree": file_tree,
        "contributors": contributors,
        "releases": releases,
    }
    cache_set(cache_k, payload, ttl_minutes=120)
    return jsonify(payload)


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    page = int(request.args.get("page", 1))
    if not q:
        return jsonify({"repos": [], "total": 0})

    cache_k = f"search:{q}:page:{page}"
    cached = cache_get(cache_k)
    if cached:
        return jsonify(cached)

    result = search_repos(q, page=page, min_stars=0)
    if result.get("_rate_limited"):
        return jsonify({"error": "rate_limited", "retry_after": result["retry_after"]}), 429

    payload = {
        "query": q,
        "total": result.get("total_count", 0),
        "page": page,
        "repos": [serialize_repo(r) for r in result.get("items", [])],
    }
    cache_set(cache_k, payload, ttl_minutes=30)
    return jsonify(payload)


@app.route("/api/trending")
def api_trending():
    """Last 7 days, high activity."""
    page = int(request.args.get("page", 1))
    language = request.args.get("language", "")

    from datetime import date, timedelta
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    query = f"created:>{week_ago}"

    cache_k = f"trending:lang:{language}:page:{page}"
    cached = cache_get(cache_k)
    if cached:
        return jsonify(cached)

    result = search_repos(query, language or None, page=page, sort="stars", min_stars=10)
    if result.get("_rate_limited"):
        return jsonify({"error": "rate_limited"}), 429

    payload = {
        "total": result.get("total_count", 0),
        "page": page,
        "repos": [serialize_repo(r) for r in result.get("items", [])],
    }
    cache_set(cache_k, payload, ttl_minutes=30)
    return jsonify(payload)


# ─── DB Export Endpoints ────────────────────────────────────────────────────────

@app.route("/api/db/cached")
def api_db_cached():
    """Return all repos that exist in the local cache — instant, no API calls."""
    all_repos = []
    seen_ids = set()

    for cache_file in CACHE_DIR.glob("*.json"):
        try:
            data = json.loads(cache_file.read_text())
            # Skip expired
            if datetime.utcnow() > datetime.fromisoformat(data["expires"]):
                continue
            payload = data.get("payload", {})
            # Repo list payloads have a "repos" key
            repos = payload.get("repos", [])
            for r in repos:
                rid = r.get("id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    all_repos.append(r)
        except Exception:
            continue

    all_repos.sort(key=lambda r: r.get("stars", 0), reverse=True)
    return jsonify({"repos": all_repos, "total": len(all_repos)})


@app.route("/api/db/sqlite", methods=["POST"])
def api_db_sqlite():
    """Accept a list of repos as JSON, build a SQLite DB, return it as a file download."""
    import sqlite3
    import tempfile

    data = request.get_json(force=True)
    repos = data.get("repos", [])

    if not repos:
        return jsonify({"error": "No repos provided"}), 400

    # Build SQLite in a temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name

    try:
        con = sqlite3.connect(db_path)
        cur = con.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS repos (
                id              INTEGER PRIMARY KEY,
                name            TEXT,
                full_name       TEXT,
                owner           TEXT,
                description     TEXT,
                url             TEXT,
                stars           INTEGER,
                forks           INTEGER,
                watchers        INTEGER,
                open_issues     INTEGER,
                language        TEXT,
                topics          TEXT,
                license         TEXT,
                is_fork         INTEGER,
                is_archived     INTEGER,
                homepage        TEXT,
                default_branch  TEXT,
                created_at      TEXT,
                updated_at      TEXT,
                pushed_at       TEXT,
                size_kb         INTEGER,
                scraped_at      TEXT
            )
        """)

        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS repos_fts USING fts5(
                name, full_name, description, topics, language,
                content=repos, content_rowid=id
            )
        """)

        now = datetime.utcnow().isoformat()
        for r in repos:
            topics_str = ",".join(r.get("topics", []))
            cur.execute("""
                INSERT OR REPLACE INTO repos VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
            """, (
                r.get("id"), r.get("name"), r.get("full_name"), r.get("owner"),
                r.get("description", ""), r.get("url", ""),
                r.get("stars", 0), r.get("forks", 0), r.get("watchers", 0),
                r.get("open_issues", 0), r.get("language", ""),
                topics_str, r.get("license", ""),
                int(r.get("is_fork", False)), int(r.get("is_archived", False)),
                r.get("homepage", ""), r.get("default_branch", "main"),
                r.get("created_at", ""), r.get("updated_at", ""),
                r.get("pushed_at", ""), r.get("size_kb", 0), now,
            ))

        # Populate FTS index
        cur.execute("INSERT INTO repos_fts(repos_fts) VALUES('rebuild')")
        con.commit()
        con.close()

        return send_file(
            db_path,
            mimetype="application/x-sqlite3",
            as_attachment=True,
            download_name=f"gitwiki_{datetime.utcnow().strftime('%Y%m%d')}.db",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        # Clean up temp file after Flask sends it (give it a moment)
        import threading
        def cleanup():
            import time; time.sleep(5)
            try: os.unlink(db_path)
            except: pass
        threading.Thread(target=cleanup, daemon=True).start()


# ─── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    token_status = "✅ Authenticated (5,000 req/hr)" if CONFIG["github_token"] else "⚠️  No token (60 req/hr) — set GITHUB_TOKEN for best experience"
    print(f"""
╔══════════════════════════════════════════════╗
║         GitHub Wiki — Local Server           ║
╚══════════════════════════════════════════════╝
  → Open: http://localhost:5000
  → Token: {token_status}
  → Cache: {CACHE_DIR.resolve()}
  → Min stars: {CONFIG['min_stars']}

  Get a token (free): https://github.com/settings/tokens
  Then: export GITHUB_TOKEN=ghp_yourtoken && python server.py
""")
    app.run(debug=False, port=5000, threaded=True)
