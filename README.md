# Danbo Nov – Booru → NovelAI Web System

Danbooru / Gelbooru の検索結果から被写体タグ（character + general）を抜き出し、**固定したベースの型**（絵柄・品質・アーティスト）に差し込んで NovelAI (V4.5) で画像生成する Web システムです。**検索〜生成までブラウザのこのページだけで完結**します（サーバ側で各APIを叩くので CORS / userscript 不要）。

## 2つの使い方

| 形 | 置き場所 | 向き |
|---|---|---|
| **Web システム（推奨・単体完結）** | `app/`（FastAPI） | ブラウザで開くだけ。サーバが Danbooru/Gelbooru/NovelAI を代理アクセス |
| Tampermonkey userscript | `userscript/` | Danbooru ページに乗せる軽量版。スマホで完結したい場合 |

## Features (Studio = トップページ `/`)

- **検索＋タグ補完**: 入力に応じて候補タグを表示（safe/nsfw 区別なし）。カテゴリ色分け＋投稿数
- **ソース切替**: Danbooru / Gelbooru（Gelbooru は `s=tag` でタグを character/general に分類）
- **ベースプリセット**: 絵柄・品質・artist の「型」を保存し、被写体を `{tags}` 位置に差し込み（NovelAI チャンク相当）
- **被写体スコープ**: character+general / characterのみ / generalのみ
- **モード**: 投稿を1件ずつ / タグをシャッフル合成
- **dry run**: 生成せずプロンプトだけ確認（トークン不要）
- 生成画像は **Gallery** に蓄積、**Dashboard**（Optuna最適化フロー）も従来通り利用可

### ルート

| パス | 内容 |
|---|---|
| `/` | **Studio**（検索→生成の単体完結ページ） |
| `/gallery` | 生成画像ギャラリー（評価UI / Optuna連携） |
| `/dashboard` | スコア推移・頻出タグの可視化 |

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
| `NAI_TOKEN` | ✅ | NovelAI Persistent API Token (`pst-***`) |
| `NAI_MODEL` | ❌ | NovelAI model (default `nai-diffusion-4-5-full`) |
| `DANBOORU_LOGIN` | ❌ | Danbooru username (higher rate limits) |
| `DANBOORU_API_KEY` | ❌ | Danbooru API key |
| `GELBOORU_API_KEY` | ➖ | Gelbooru API key (required to use the Gelbooru source) |
| `GELBOORU_USER_ID` | ➖ | Gelbooru user id (required to use the Gelbooru source) |
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

## Usage（Studio / トップページ）

1. **ソース**を選ぶ（Danbooru / Gelbooru）。Gelbooru を使うなら `GELBOORU_*` を設定
2. **検索タグ**を入力（候補が safe/nsfw 区別なく出る）。複数はスペース区切り
3. **ベース**（型）を用意 — 例 `artist:wlop, very aesthetic, best quality, {tags}`。`{tags}` に被写体が入る
4. **被写体タグ**スコープ・**モード**・件数を選ぶ
5. まず **dry run** にチェックして「ベース＋被写体」のプロンプトを確認（トークン不要）
6. 問題なければチェックを外して **▶ 生成開始**（Anlas 消費の確認あり）。結果は `/gallery` にも残る

> ベースプリセットとネガティブはブラウザの localStorage に保存されます。

## Architecture

```
app/
├── main.py              # FastAPI application & routes (Studio + APIs)
├── config.py            # Configuration from environment
├── models.py            # SQLAlchemy models (GeneratedImage, TagHistory)
├── services/
│   ├── booru.py         # Danbooru/Gelbooru: subjects + autocomplete (Studio)
│   ├── danbooru.py      # Danbooru tag sampling (optimizer flow)
│   ├── novelai.py       # NovelAI API client (V4.5 payload)
│   └── optimizer.py     # Optuna-based prompt optimization engine
└── templates/
    ├── base.html         # Base template (Tailwind CSS)
    ├── studio.html       # Studio: search → base → generate (standalone)
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