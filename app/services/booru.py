"""Unified booru source service (Danbooru + Gelbooru).

Runs server-side, so there are no CORS limits and no userscript needed.
Each source normalises posts to ``{"id", "character": [...], "general": [...]}``
so the rest of the pipeline is source-agnostic. Only character + general
tags are kept; artist / copyright / meta are dropped (style/quality come
from the base preset, like NovelAI "chunks").
"""

import logging
from typing import Any

import httpx

from app.config import (
    DANBOORU_API_KEY,
    DANBOORU_LOGIN,
    GELBOORU_API_KEY,
    GELBOORU_USER_ID,
)

logger = logging.getLogger(__name__)

DANBOORU_BASE = "https://danbooru.donmai.us"
GELBOORU_BASE = "https://gelbooru.com"

# Gelbooru tag type codes
_GEL_CHARACTER = 4
_GEL_GENERAL = 0

# Fallback meta blacklist used only when Gelbooru classification is unavailable
_META_FALLBACK = {
    "highres", "absurdres", "lowres", "commentary", "commentary_request",
    "translated", "translation_request", "bad_id", "bad_pixiv_id", "tagme",
}


def _split(s: str | None) -> list[str]:
    return [t for t in (s or "").split() if t]


def _danbooru_auth(params: dict[str, Any]) -> dict[str, Any]:
    if DANBOORU_LOGIN and DANBOORU_API_KEY:
        params["login"] = DANBOORU_LOGIN
        params["api_key"] = DANBOORU_API_KEY
    return params


def _gelbooru_auth(params: dict[str, Any]) -> dict[str, Any]:
    if GELBOORU_API_KEY and GELBOORU_USER_ID:
        params["api_key"] = GELBOORU_API_KEY
        params["user_id"] = GELBOORU_USER_ID
    return params


# ---------------------------------------------------------------------------
# Subjects (post -> {id, character, general})
# ---------------------------------------------------------------------------


async def _fetch_danbooru(tags: str, limit: int) -> list[dict[str, Any]]:
    query = tags if "order:" in tags else f"{tags} order:random"
    params = _danbooru_auth({"tags": query, "limit": limit})
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{DANBOORU_BASE}/posts.json", params=params)
        resp.raise_for_status()
        posts = resp.json()
    if not isinstance(posts, list):
        return []
    return [
        {
            "id": p.get("id"),
            "character": _split(p.get("tag_string_character")),
            "general": _split(p.get("tag_string_general")),
        }
        for p in posts
    ]


async def _gelbooru_classify(
    client: httpx.AsyncClient, names: list[str]
) -> dict[str, int]:
    """Map tag name -> type via Gelbooru s=tag API (batched)."""
    type_map: dict[str, int] = {}
    chunk = 80
    for i in range(0, len(names), chunk):
        part = names[i : i + chunk]
        params = _gelbooru_auth(
            {
                "page": "dapi", "s": "tag", "q": "index", "json": "1",
                "limit": len(part), "names": " ".join(part),
            }
        )
        try:
            resp = await client.get(f"{GELBOORU_BASE}/index.php", params=params)
            resp.raise_for_status()
            data = resp.json()
            tags = data if isinstance(data, list) else data.get("tag", [])
            for t in tags:
                type_map[t["name"]] = int(t.get("type", 0))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.warning("Gelbooru tag classify failed: %s", exc)
    return type_map


async def _fetch_gelbooru(tags: str, limit: int) -> list[dict[str, Any]]:
    query = tags if "sort:" in tags else f"{tags} sort:random"
    params = _gelbooru_auth(
        {
            "page": "dapi", "s": "post", "q": "index", "json": "1",
            "limit": limit, "tags": query,
        }
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{GELBOORU_BASE}/index.php", params=params)
        resp.raise_for_status()
        data = resp.json()
        posts = data if isinstance(data, list) else data.get("post", [])

        uniq = sorted({t for p in posts for t in _split(p.get("tags"))})
        type_map = await _gelbooru_classify(client, uniq)

    items: list[dict[str, Any]] = []
    for p in posts:
        all_tags = _split(p.get("tags"))
        character: list[str] = []
        general: list[str] = []
        classified = False
        for t in all_tags:
            ty = type_map.get(t)
            if ty is None:
                continue
            classified = True
            if ty == _GEL_CHARACTER:
                character.append(t)
            elif ty == _GEL_GENERAL:
                general.append(t)
        if not classified:
            general = [t for t in all_tags if t not in _META_FALLBACK]
        items.append({"id": p.get("id"), "character": character, "general": general})
    return items


async def fetch_subjects(source: str, tags: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return normalised subject items for the given source."""
    try:
        if source == "gelbooru":
            return await _fetch_gelbooru(tags, limit)
        return await _fetch_danbooru(tags, limit)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("fetch_subjects(%s) error: %s", source, exc)
        return []


# ---------------------------------------------------------------------------
# Autocomplete (search candidates; rating-agnostic)
# ---------------------------------------------------------------------------


async def autocomplete(source: str, query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Tag suggestions for a prefix. Tags carry no rating, so safe + nsfw
    candidates are both returned."""
    if not query:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if source == "gelbooru":
                params = _gelbooru_auth(
                    {"page": "autocomplete2", "type": "tag_query",
                     "limit": limit, "term": query}
                )
                resp = await client.get(f"{GELBOORU_BASE}/index.php", params=params)
                resp.raise_for_status()
                data = resp.json()
                rows = data if isinstance(data, list) else []
                return [
                    {
                        "value": r.get("value") or r.get("label"),
                        "category": int(r.get("category", 0)),
                        "count": int(r.get("post_count", 0) or 0),
                    }
                    for r in rows
                ]
            params = _danbooru_auth(
                {"search[query]": query, "search[type]": "tag_query", "limit": limit}
            )
            resp = await client.get(f"{DANBOORU_BASE}/autocomplete.json", params=params)
            resp.raise_for_status()
            rows = resp.json()
            return [
                {
                    "value": r.get("value"),
                    "category": int(r.get("category", 0)),
                    "count": int(r.get("post_count", 0) or 0),
                }
                for r in (rows if isinstance(rows, list) else [])
            ]
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("autocomplete(%s) error: %s", source, exc)
        return []
