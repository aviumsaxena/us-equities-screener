from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from api.config import settings

engine: AsyncEngine = create_async_engine(
    settings.async_database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)
