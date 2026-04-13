# Danbo Nov – AI Image Prompt Optimization System

Optunaを使ってNovelAI画像生成のプロンプトを自動最適化するWebシステムです。

## Features

- **Dynamic Tag Sampling**: Danbooru APIから関連タグを動的に取得
- **Prompt Optimization**: Optunaによるプロンプトの自動最適化
- **Image Rating UI**: 0-5点の評価をブラウザ上で直感的に送信
- **Keyboard Shortcuts**: `1`-`5`キーで素早く評価、`G`キーで画像生成
- **Dashboard**: スコア推移・頻出タグ・ベストトライアルの可視化

## Quick Start

### 1. Install dependencies

```bash
# Using uv (recommended)
uv sync

# Or using pip
pip install -e .
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env and set your NAI_TOKEN
```

| Variable | Required | Description |
|---|---|---|
| `NAI_TOKEN` | ✅ | NovelAI API Bearer Token |
| `DANBOORU_LOGIN` | ❌ | Danbooru username (higher rate limits) |
| `DANBOORU_API_KEY` | ❌ | Danbooru API key |
| `HOST` | ❌ | Server bind address (default: `0.0.0.0`) |
| `PORT` | ❌ | Server port (default: `8000`) |

### 3. Run the server

```bash
# Using uv
uv run uvicorn app.main:app --reload

# Or directly
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 in your browser.

## Usage

1. **Generate**: Enter a base keyword (e.g., `1girl`, `landscape`) and click Generate
2. **Rate**: Click an image to select it, then click a score button (0-5) or press `0`-`5` on your keyboard
3. **Iterate**: The optimizer learns from your ratings — high-scored tags get emphasized, low-scored tags move to the negative prompt
4. **Monitor**: Visit the Dashboard to see score trends and top-performing tags

## Architecture

```
app/
├── main.py              # FastAPI application & routes
├── config.py            # Configuration from environment
├── models.py            # SQLAlchemy models (GeneratedImage, TagHistory)
├── services/
│   ├── danbooru.py      # Danbooru API client for tag sampling
│   ├── novelai.py       # NovelAI API client for image generation
│   └── optimizer.py     # Optuna-based prompt optimization engine
└── templates/
    ├── base.html         # Base template (Tailwind CSS)
    ├── index.html        # Gallery page with rating UI
    └── dashboard.html    # Optimization dashboard
```

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `0`-`5` | Rate selected image |
| `G` | Generate new image |
| Click image | Select for keyboard rating |

## NovelAI Prompt Syntax

The optimizer uses NovelAI's tag weighting syntax:
- `{tag}` — 1.05× emphasis
- `{{tag}}` — 1.10× emphasis
- `[tag]` — 0.95× de-emphasis

High-scoring tags automatically receive `{{}}` emphasis.
Low-scoring tags are moved to the negative prompt.

## Tech Stack

- **Backend**: FastAPI, SQLAlchemy (async), Optuna, httpx
- **Frontend**: Jinja2 templates, Tailwind CSS (CDN)
- **Database**: SQLite (application data) + SQLite (Optuna storage)