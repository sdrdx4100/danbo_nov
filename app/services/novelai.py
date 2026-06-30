"""NovelAI image generation, built on the novelai-sdk (``novelai`` package).

The SDK owns the NovelAI protocol (auth, the V4.5 payload, decoding,
character prompts). This module maps our config/inputs onto
``GenerateImageParams`` and saves the returned image into ``static/``.
"""

import inspect
import logging
import random
import uuid
from pathlib import Path
from typing import Any

from novelai import AsyncNovelAI
from novelai.types import Character, GenerateImageParams

from app.config import (
    NAI_CFG_SCALE,
    NAI_HEIGHT,
    NAI_MODEL,
    NAI_SAMPLER,
    NAI_STEPS,
    NAI_TOKEN,
    NAI_WIDTH,
    STATIC_DIR,
)

logger = logging.getLogger(__name__)

# Models accepted by GenerateImageParams (Literal). Anything else falls back.
_VALID_MODELS = {
    "nai-diffusion-4-5-full",
    "nai-diffusion-4-5-curated",
    "nai-diffusion-4-full",
    "nai-diffusion-4-curated",
    "nai-diffusion-3",
    "nai-diffusion-3-furry",
}


def _model() -> str:
    if NAI_MODEL in _VALID_MODELS:
        return NAI_MODEL
    logger.warning("Unknown NAI_MODEL=%r, falling back to nai-diffusion-4-5-full", NAI_MODEL)
    return "nai-diffusion-4-5-full"


def _characters(character_prompts: list[dict[str, Any]] | None) -> list[Character]:
    chars: list[Character] = []
    for c in character_prompts or []:
        prompt = (c.get("prompt") or "").strip()
        if not prompt:
            continue
        chars.append(Character(prompt=prompt, negative_prompt=(c.get("uc") or "")))
    return chars


async def generate_image(
    positive_prompt: str,
    negative_prompt: str = "",
    *,
    width: int = NAI_WIDTH,
    height: int = NAI_HEIGHT,
    steps: int = NAI_STEPS,
    cfg_scale: float = NAI_CFG_SCALE,
    sampler: str = NAI_SAMPLER,
    seed: int | None = None,
    character_prompts: list[dict[str, Any]] | None = None,
) -> str | None:
    """Generate an image via the NovelAI SDK and save it to ``static/``.

    ``character_prompts`` is an optional list of ``{"prompt", "uc"}`` dicts
    for V4.5 per-character prompting. Returns the saved filename, or None.
    """
    if not NAI_TOKEN:
        logger.error("NAI_TOKEN is not configured")
        return None

    if seed is None:
        seed = random.randint(0, 2**32 - 1)

    chars = _characters(character_prompts)
    params = GenerateImageParams(
        prompt=positive_prompt,
        model=_model(),
        negative_prompt=negative_prompt or None,
        size=(width, height),
        steps=steps,
        scale=cfg_scale,
        sampler=sampler,
        seed=seed,
        characters=chars or None,
    )

    client = AsyncNovelAI(api_key=NAI_TOKEN)
    try:
        images = await client.image.generate(params)
    except Exception as exc:  # SDK raises NovelAIError subclasses
        logger.error("NovelAI generation failed: %s", exc)
        return None
    finally:
        closer = client.close()
        if inspect.isawaitable(closer):
            await closer

    if not images:
        logger.error("NovelAI returned no images")
        return None

    filename = f"{uuid.uuid4().hex}.png"
    images[0].save(Path(STATIC_DIR) / filename, format="PNG")
    logger.info("Saved generated image to %s", filename)
    return filename
