"""NovelAI API service for image generation."""

import io
import logging
import uuid
import zipfile
from pathlib import Path

import httpx

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

NAI_API_URL = "https://image.api.novelai.net/ai/generate-image"


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
) -> str | None:
    """Generate an image via NovelAI API and save to static/.

    Returns the relative path to the saved image, or None on failure.
    """
    if not NAI_TOKEN:
        logger.error("NAI_TOKEN is not configured")
        return None

    import random

    if seed is None:
        seed = random.randint(0, 2**32 - 1)

    payload = {
        "input": positive_prompt,
        "model": NAI_MODEL,
        "action": "generate",
        "parameters": {
            "width": width,
            "height": height,
            "scale": cfg_scale,
            "sampler": sampler,
            "steps": steps,
            "seed": seed,
            "n_samples": 1,
            "negative_prompt": negative_prompt,
            "ucPreset": 0,
            "qualityToggle": True,
            "sm": False,
            "sm_dyn": False,
            "dynamic_thresholding": False,
            "noise_schedule": "native",
        },
    }

    headers = {
        "Authorization": f"Bearer {NAI_TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            resp = await client.post(NAI_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("NovelAI API error: %s", exc)
            return None

    # NAI returns a zip file containing the image
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            image_names = [n for n in zf.namelist() if n.endswith(".png")]
            if not image_names:
                logger.error("No PNG found in NAI response")
                return None
            image_data = zf.read(image_names[0])
    except (zipfile.BadZipFile, KeyError) as exc:
        logger.error("Failed to extract image from NAI response: %s", exc)
        return None

    # Save image
    filename = f"{uuid.uuid4().hex}.png"
    filepath = Path(STATIC_DIR) / filename
    filepath.write_bytes(image_data)
    logger.info("Saved generated image to %s", filepath)

    return filename
