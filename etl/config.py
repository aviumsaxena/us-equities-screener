from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://screener:screener@localhost:5432/screener"
    redis_url: str = "redis://localhost:6379/0"
    sec_edgar_user_agent: str = "Screener ETL you@example.com"
    bronze_path: str = "data/bronze"

    # EOD price vendor (Alpha Vantage). Free key issued instantly (no email
    # confirmation) at https://www.alphavantage.co/support/#api-key; set in
    # .env (gitignored) -- never commit it.
    alphavantage_api_key: str = ""


settings = Settings()
