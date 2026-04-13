"""Database models and session management."""

from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class GeneratedImage(Base):
    """Stores generated images with their prompts and metadata."""

    __tablename__ = "generated_images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    positive_prompt = Column(Text, nullable=False)
    negative_prompt = Column(Text, nullable=False, default="")
    image_path = Column(String(500), nullable=False)
    score = Column(Float, nullable=True)  # 0-5 rating
    optuna_trial_id = Column(Integer, nullable=True)
    tags_json = Column(Text, nullable=False, default="[]")  # JSON list of tags used
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class TagHistory(Base):
    """Tracks tag performance across generations."""

    __tablename__ = "tag_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tag = Column(String(200), nullable=False, index=True)
    avg_score = Column(Float, nullable=False, default=0.0)
    usage_count = Column(Integer, nullable=False, default=0)
    last_used = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


async def init_db() -> None:
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI endpoints."""
    async with async_session() as session:
        yield session
