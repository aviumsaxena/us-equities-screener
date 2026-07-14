from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://screener:screener@localhost:5432/screener"
    redis_url: str = "redis://localhost:6379/0"
    sec_edgar_user_agent: str = "Screener ETL you@example.com"
    bronze_path: str = "data/bronze"

    # EOD price vendor (Polygon). Free key at https://polygon.io (now
    # massive.com); set in .env (gitignored) -- never commit it. Sent as an
    # Authorization: Bearer header, so it never appears in a URL.
    polygon_api_key: str = ""


settings = Settings()
