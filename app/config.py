"""Application configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

NAI_TOKEN: str = os.getenv("NAI_TOKEN", "")
DANBOORU_LOGIN: str = os.getenv("DANBOORU_LOGIN", "")
DANBOORU_API_KEY: str = os.getenv("DANBOORU_API_KEY", "")

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./danbo_nov.db")
OPTUNA_STUDY_NAME: str = os.getenv("OPTUNA_STUDY_NAME", "prompt_optimization")

HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))

# NovelAI image generation defaults
NAI_MODEL: str = "nai-diffusion-4-curated-preview"
NAI_WIDTH: int = 832
NAI_HEIGHT: int = 1216
NAI_STEPS: int = 28
NAI_CFG_SCALE: float = 5.0
NAI_SAMPLER: str = "k_euler_ancestral"
