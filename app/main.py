"""FastAPI application – AI Image Prompt Optimization System."""

import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Body, Depends, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import STATIC_DIR
from app.models import GeneratedImage, get_session, init_db
from app.services.booru import autocomplete, fetch_subjects
from app.services.danbooru import sample_tags_for_keyword
from app.services.novelai import generate_image
from app.services.optimizer import (
    PromptOptimizer,
    get_tag_frequency_stats,
    update_tag_history,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global optimizer instance
optimizer = PromptOptimizer()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan – initialise DB on startup."""
    await init_db()
    logger.info("Database initialised")
    yield


app = FastAPI(title="Danbo Nov – Prompt Optimizer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(
    directory=str(STATIC_DIR.parent / "app" / "templates")
)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def studio(request: Request):
    """Standalone Studio – search booru, build prompt from a fixed base,
    generate via NovelAI. Self-contained (no userscript / no CORS)."""
    return templates.TemplateResponse(request=request, name="studio.html", context={})


@app.get("/gallery", response_class=HTMLResponse)
async def gallery(
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Gallery of generated/rated images (optimizer flow)."""
    stmt = select(GeneratedImage).order_by(GeneratedImage.id.desc()).limit(50)
    result = await db.execute(stmt)
    images = result.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"images": images},
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Optimization dashboard page."""
    score_history = optimizer.get_score_history()
    best_trials = optimizer.get_best_trials()
    tag_stats = await get_tag_frequency_stats(db)

    # Basic aggregate stats
    stmt = select(
        func.count(GeneratedImage.id),
        func.avg(GeneratedImage.score),
    ).where(GeneratedImage.score.isnot(None))
    result = await db.execute(stmt)
    row = result.one()
    rated_count = row[0] or 0
    avg_score = round(row[1], 2) if row[1] else 0.0

    total_stmt = select(func.count(GeneratedImage.id))
    total_result = await db.execute(total_stmt)
    total_count = total_result.scalar() or 0

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "score_history": score_history,
            "best_trials": best_trials,
            "tag_stats": tag_stats,
            "rated_count": rated_count,
            "total_count": total_count,
            "avg_score": avg_score,
        },
    )


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.post("/api/generate")
async def api_generate(
    keyword: str = Form("1girl"),
    db: AsyncSession = Depends(get_session),
):
    """Generate an image with optimised prompt."""
    # 1. Sample tags from Danbooru
    sampled_tags = await sample_tags_for_keyword(keyword, count=15)
    optimizer.update_candidate_tags(sampled_tags)

    # 2. Update tag scores from DB
    await optimizer.update_tag_scores(db)

    # 3. Get optimised prompt from Optuna
    positive, negative, selected_tags, trial_number = optimizer.suggest_prompt(
        base_keyword=keyword
    )

    # 4. Generate image via NovelAI
    filename = await generate_image(positive, negative)
    if filename is None:
        return JSONResponse(
            {"error": "Image generation failed. Check NAI_TOKEN and API status."},
            status_code=502,
        )

    # 5. Save to database
    record = GeneratedImage(
        positive_prompt=positive,
        negative_prompt=negative,
        image_path=filename,
        optuna_trial_id=trial_number,
        tags_json=json.dumps(selected_tags),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return JSONResponse(
        {
            "id": record.id,
            "image_url": f"/static/{filename}",
            "positive_prompt": positive,
            "negative_prompt": negative,
            "tags": selected_tags,
            "trial": trial_number,
        }
    )


# ---------------------------------------------------------------------------
# Studio API (standalone pipeline)
# ---------------------------------------------------------------------------


@app.get("/api/autocomplete")
async def api_autocomplete(
    q: str = Query(""),
    source: str = Query("danbooru"),
):
    """Tag suggestions (rating-agnostic) for the Studio search box."""
    items = await autocomplete(source, q.strip())
    return JSONResponse({"items": items})


@app.get("/api/subjects")
async def api_subjects(
    tags: str = Query(""),
    source: str = Query("danbooru"),
    limit: int = Query(20, ge=1, le=100),
):
    """Fetch posts and return normalised {id, character[], general[]} items."""
    items = await fetch_subjects(source, tags.strip(), limit)
    return JSONResponse({"source": source, "count": len(items), "items": items})


@app.post("/api/studio/generate")
async def api_studio_generate(
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_session),
):
    """Generate one image from a pre-built prompt and store it."""
    positive = (payload.get("positive") or "").strip()
    negative = (payload.get("negative") or "").strip()
    tags = payload.get("tags") or []
    character_prompts = payload.get("character_prompts") or None
    if not positive:
        return JSONResponse({"error": "positive prompt is empty"}, status_code=400)

    filename = await generate_image(
        positive, negative, character_prompts=character_prompts
    )
    if filename is None:
        return JSONResponse(
            {"error": "Image generation failed. Check NAI_TOKEN and API status."},
            status_code=502,
        )

    record = GeneratedImage(
        positive_prompt=positive,
        negative_prompt=negative,
        image_path=filename,
        tags_json=json.dumps(tags),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return JSONResponse(
        {
            "id": record.id,
            "image_url": f"/static/{filename}",
            "positive_prompt": positive,
            "negative_prompt": negative,
        }
    )


@app.post("/api/rate/{image_id}")
async def api_rate(
    image_id: int,
    score: float = Form(...),
    db: AsyncSession = Depends(get_session),
):
    """Rate a generated image (0-5)."""
    if not 0 <= score <= 5:
        return JSONResponse({"error": "Score must be between 0 and 5"}, status_code=400)

    stmt = select(GeneratedImage).where(GeneratedImage.id == image_id)
    result = await db.execute(stmt)
    image = result.scalar_one_or_none()
    if image is None:
        return JSONResponse({"error": "Image not found"}, status_code=404)

    image.score = score
    await db.commit()

    # Report to Optuna
    if image.optuna_trial_id is not None:
        optimizer.report_score(image.optuna_trial_id, score)

    # Update tag history
    tags = json.loads(image.tags_json) if image.tags_json else []
    await update_tag_history(db, tags, score)

    return JSONResponse({"status": "ok", "image_id": image_id, "score": score})


@app.get("/api/tags/sample")
async def api_sample_tags(keyword: str = Query("1girl")):
    """Sample tags from Danbooru for a keyword."""
    tags = await sample_tags_for_keyword(keyword, count=20)
    return JSONResponse({"keyword": keyword, "tags": tags})


@app.get("/api/stats")
async def api_stats(db: AsyncSession = Depends(get_session)):
    """Get optimization statistics."""
    score_history = optimizer.get_score_history()
    tag_stats = await get_tag_frequency_stats(db)
    best_trials = optimizer.get_best_trials()
    return JSONResponse(
        {
            "score_history": score_history,
            "tag_stats": tag_stats,
            "best_trials": best_trials,
        }
    )
