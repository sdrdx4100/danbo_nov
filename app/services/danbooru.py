"""Danbooru API service for dynamic tag sampling."""

import logging
from typing import Any

import httpx

from app.config import DANBOORU_API_KEY, DANBOORU_LOGIN

logger = logging.getLogger(__name__)

DANBOORU_BASE_URL = "https://danbooru.donmai.us"


async def search_tags(keyword: str, limit: int = 50) -> list[dict[str, Any]]:
    """Search Danbooru for tags related to a keyword using order:random.

    Uses the posts endpoint with tag search to discover related tags
    dynamically.
    """
    params: dict[str, Any] = {
        "tags": f"{keyword} order:random",
        "limit": limit,
    }
    if DANBOORU_LOGIN and DANBOORU_API_KEY:
        params["login"] = DANBOORU_LOGIN
        params["api_key"] = DANBOORU_API_KEY

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(
                f"{DANBOORU_BASE_URL}/posts.json", params=params
            )
            resp.raise_for_status()
            posts = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Danbooru API error: %s", exc)
            return []

    # Extract and count tags from returned posts
    tag_counter: dict[str, int] = {}
    for post in posts:
        tag_string = post.get("tag_string_general", "") or post.get(
            "tag_string", ""
        )
        for tag in tag_string.split():
            tag_counter[tag] = tag_counter.get(tag, 0) + 1

    # Sort by frequency and return
    sorted_tags = sorted(tag_counter.items(), key=lambda x: x[1], reverse=True)
    return [{"name": name, "count": count} for name, count in sorted_tags]


async def get_popular_tags(category: int = 0, limit: int = 100) -> list[str]:
    """Fetch popular general tags from Danbooru.

    Args:
        category: 0=general, 1=artist, 3=copyright, 4=character, 5=meta
        limit: Max number of tags to return.
    """
    params: dict[str, Any] = {
        "search[category]": category,
        "search[order]": "count",
        "limit": limit,
    }
    if DANBOORU_LOGIN and DANBOORU_API_KEY:
        params["login"] = DANBOORU_LOGIN
        params["api_key"] = DANBOORU_API_KEY

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(
                f"{DANBOORU_BASE_URL}/tags.json", params=params
            )
            resp.raise_for_status()
            tags = resp.json()
            return [t["name"] for t in tags if isinstance(t, dict)]
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Danbooru tags API error: %s", exc)
            return []


async def sample_tags_for_keyword(keyword: str, count: int = 10) -> list[str]:
    """Sample random related tags for a keyword.

    Returns a list of tag names suitable for use in NovelAI prompts.
    """
    results = await search_tags(keyword, limit=30)
    # Filter out very generic or meta tags
    blacklist = {
        "highres",
        "absurdres",
        "commentary",
        "commentary_request",
        "translated",
        "translation_request",
        "bad_id",
        "bad_pixiv_id",
        "tagme",
    }
    filtered = [t["name"] for t in results if t["name"] not in blacklist]
    return filtered[:count]
