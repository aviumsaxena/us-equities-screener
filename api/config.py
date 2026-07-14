from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Same DATABASE_URL the ETL uses; the API normalizes it to the asyncpg
    # driver (see async_database_url) since FastAPI reads are async I/O.
    database_url: str = "postgresql+psycopg://screener:screener@localhost:5432/screener"
    redis_url: str = "redis://localhost:6379/0"

    # cache-aside TTL safety net; correctness comes from the versioned key
    # bumped on each GOLD refresh (see api/cache.py + etl/cache.py)
    cache_ttl_seconds: int = 86400

    screen_default_limit: int = 100
    screen_max_limit: int = 500

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        if "+asyncpg" in url:
            return url
        if "+psycopg" in url:
            return url.replace("+psycopg", "+asyncpg")
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()
