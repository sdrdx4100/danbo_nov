"""Optuna-based prompt optimization engine."""

import json
import logging
import random
from collections import defaultdict

import optuna
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import OPTUNA_STUDY_NAME
from app.models import GeneratedImage, TagHistory

logger = logging.getLogger(__name__)

# Suppress Optuna's verbose logging
optuna.logging.set_verbosity(optuna.logging.WARNING)

# Default tag pools – extended dynamically from Danbooru results
DEFAULT_QUALITY_TAGS = [
    "masterpiece",
    "best quality",
    "very aesthetic",
    "absurdres",
]

DEFAULT_NEGATIVE_TAGS = [
    "lowres",
    "bad anatomy",
    "bad hands",
    "text",
    "error",
    "missing fingers",
    "extra digit",
    "fewer digits",
    "cropped",
    "worst quality",
    "low quality",
    "normal quality",
    "jpeg artifacts",
    "signature",
    "watermark",
    "username",
    "blurry",
]


def _get_or_create_study() -> optuna.Study:
    """Get or create the Optuna study for prompt optimization."""
    storage = optuna.storages.RDBStorage(
        url="sqlite:///optuna_study.db",
        engine_kwargs={"connect_args": {"check_same_thread": False}},
    )
    return optuna.create_study(
        study_name=OPTUNA_STUDY_NAME,
        storage=storage,
        direction="maximize",
        load_if_exists=True,
    )


class PromptOptimizer:
    """Manages prompt optimization using Optuna."""

    def __init__(self) -> None:
        self.study = _get_or_create_study()
        self._candidate_tags: list[str] = []
        self._low_score_tags: set[str] = set()
        self._high_score_tags: set[str] = set()

    def update_candidate_tags(self, tags: list[str]) -> None:
        """Update the pool of candidate tags from Danbooru sampling."""
        existing = set(self._candidate_tags)
        for tag in tags:
            if tag not in existing:
                self._candidate_tags.append(tag)
                existing.add(tag)

    async def update_tag_scores(self, db: AsyncSession) -> None:
        """Refresh tag performance data from the database."""
        self._low_score_tags.clear()
        self._high_score_tags.clear()

        stmt = select(TagHistory).where(TagHistory.usage_count >= 2)
        result = await db.execute(stmt)
        for row in result.scalars():
            if row.avg_score <= 2.0:
                self._low_score_tags.add(row.tag)
            elif row.avg_score >= 4.0:
                self._high_score_tags.add(row.tag)

    def suggest_prompt(
        self, base_keyword: str = ""
    ) -> tuple[str, str, list[str], int]:
        """Use Optuna to suggest an optimized prompt.

        Returns:
            (positive_prompt, negative_prompt, selected_tags, trial_number)
        """
        trial = self.study.ask()

        # --- Select tags from candidate pool ---
        available = [
            t for t in self._candidate_tags if t not in self._low_score_tags
        ]
        if not available:
            available = self._candidate_tags or ["1girl", "solo"]

        num_tags = trial.suggest_int("num_tags", 3, min(12, len(available)))

        # Deterministic selection based on trial params
        selected_indices: list[int] = []
        for i in range(min(num_tags, len(available))):
            idx = trial.suggest_int(f"tag_{i}", 0, len(available) - 1)
            selected_indices.append(idx)

        selected_tags = list({available[i] for i in selected_indices})

        # --- Build positive prompt with NovelAI weighting syntax ---
        prompt_parts: list[str] = list(DEFAULT_QUALITY_TAGS)
        if base_keyword:
            prompt_parts.append(base_keyword)

        for tag in selected_tags:
            display_tag = tag.replace("_", " ")
            if tag in self._high_score_tags:
                # Emphasise high-scoring tags with {{}} (NovelAI syntax)
                prompt_parts.append(f"{{{{{display_tag}}}}}")
            else:
                # Apply random mild emphasis
                weight_level = trial.suggest_int(f"w_{tag}", 0, 2)
                if weight_level == 0:
                    prompt_parts.append(display_tag)
                elif weight_level == 1:
                    prompt_parts.append(f"{{{display_tag}}}")
                else:
                    prompt_parts.append(f"{{{{{display_tag}}}}}")

        positive_prompt = ", ".join(prompt_parts)

        # --- Build negative prompt ---
        neg_parts = list(DEFAULT_NEGATIVE_TAGS)
        for tag in self._low_score_tags:
            display_tag = tag.replace("_", " ")
            if display_tag not in neg_parts:
                neg_parts.append(display_tag)

        negative_prompt = ", ".join(neg_parts)

        return positive_prompt, negative_prompt, selected_tags, trial.number

    def report_score(self, trial_number: int, score: float) -> None:
        """Report user evaluation score back to Optuna."""
        try:
            self.study.tell(trial_number, score)
            logger.info(
                "Reported score %.1f for trial %d", score, trial_number
            )
        except Exception as exc:
            logger.warning("Failed to report score to Optuna: %s", exc)

    def get_best_trials(self, top_n: int = 5) -> list[dict]:
        """Get the top-performing trials."""
        try:
            trials = sorted(
                [t for t in self.study.trials if t.value is not None],
                key=lambda t: t.value or 0,
                reverse=True,
            )
            return [
                {"number": t.number, "value": t.value, "params": t.params}
                for t in trials[:top_n]
            ]
        except Exception:
            return []

    def get_score_history(self) -> list[dict]:
        """Get score progression for dashboard."""
        return [
            {"trial": t.number, "score": t.value}
            for t in self.study.trials
            if t.value is not None
        ]


async def update_tag_history(
    db: AsyncSession, tags: list[str], score: float
) -> None:
    """Update tag performance statistics in the database."""
    from datetime import datetime, timezone

    for tag in tags:
        stmt = select(TagHistory).where(TagHistory.tag == tag)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            total = existing.avg_score * existing.usage_count + score
            existing.usage_count += 1
            existing.avg_score = total / existing.usage_count
            existing.last_used = datetime.now(timezone.utc)
        else:
            new_tag = TagHistory(
                tag=tag,
                avg_score=score,
                usage_count=1,
                last_used=datetime.now(timezone.utc),
            )
            db.add(new_tag)

    await db.commit()


async def get_tag_frequency_stats(
    db: AsyncSession, limit: int = 20
) -> list[dict]:
    """Get most frequently used tags and their average scores."""
    stmt = (
        select(TagHistory)
        .order_by(TagHistory.usage_count.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [
        {
            "tag": row.tag,
            "avg_score": round(row.avg_score, 2),
            "count": row.usage_count,
        }
        for row in result.scalars()
    ]
