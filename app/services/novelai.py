"""NovelAI image generation, built on the novelai-python SDK.

The SDK owns the NovelAI protocol (auth, the V4.5 payload, ZIP→PNG
extraction, character_prompts). This module is a thin wrapper that maps
our config/inputs onto it and saves the result into ``static/``.
"""

import logging
import random
import uuid
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from novelai_python import ApiCredential
from novelai_python.sdk.ai.generate_image import (
    Character,
    GenerateImageInfer,
    Model,
    Sampler,
)

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


def _model() -> Model:
    try:
        return Model(NAI_MODEL)
    except ValueError:
        logger.warning("Unknown NAI_MODEL=%r, falling back to V4.5 full", NAI_MODEL)
        return Model.NAI_DIFFUSION_4_5_FULL


def _sampler(name: str) -> Sampler:
    try:
        return Sampler(name)
    except ValueError:
        return Sampler.K_EULER_ANCESTRAL


def _characters(character_prompts: list[dict[str, Any]] | None) -> list[Character]:
    chars: list[Character] = []
    for c in character_prompts or []:
        prompt = (c.get("prompt") or "").strip()
        if not prompt:
            continue
        chars.append(Character(prompt=prompt, uc=(c.get("uc") or "")))
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

    credential = ApiCredential(api_token=SecretStr(NAI_TOKEN))
    chars = _characters(character_prompts)

    gen = GenerateImageInfer.build_generate(
        prompt=positive_prompt,
        model=_model(),
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        steps=steps,
        sampler=_sampler(sampler),
        seed=seed,
        character_prompts=chars or None,
        qualityToggle=True,
    )
    # build_generate has no cfg-scale arg; set it on the parameters directly
    if hasattr(gen.parameters, "scale"):
        gen.parameters.scale = cfg_scale

    try:
        resp = await gen.request(session=credential)
    except Exception as exc:  # SDK raises NovelAiError subclasses
        logger.error("NovelAI generation failed: %s", exc)
        return None

    if not resp.files:
        logger.error("NovelAI returned no files")
        return None

    _name, data = resp.files[0]
    filename = f"{uuid.uuid4().hex}.png"
    (Path(STATIC_DIR) / filename).write_bytes(data)
    logger.info("Saved generated image to %s", filename)
    return filename
