"""
Async SQLAlchemy session factory — shared by both bot_main.py and web_main.py.
Each process creates its own engine and session factory using the same DATABASE_URL.
SSL is enabled via connect_args when the original URL contained sslmode=require,
because asyncpg does not accept sslmode as a URL query parameter.
"""
import os
import ssl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

# Detect whether the raw DATABASE_URL asked for SSL so we can pass it the
# asyncpg-correct way (connect_args) rather than as a URL query param.
_raw_url = os.getenv("DATABASE_URL", "")
_wants_ssl = "sslmode=require" in _raw_url or "sslmode=verify" in _raw_url

_connect_args: dict = {}
if _wants_ssl:
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE
    _connect_args["ssl"] = _ssl_ctx

engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "development",
    pool_pre_ping=True,
    connect_args=_connect_args,
    poolclass=NullPool if settings.is_test else None,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_session() -> AsyncSession:
    """FastAPI dependency — yields a session and closes it after the request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db_session() -> AsyncSession:
    """Direct session for non-FastAPI use (bot, background jobs)."""
    return AsyncSessionLocal()
