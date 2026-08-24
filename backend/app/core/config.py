from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="ARD Suite API", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")

    db_host: str = Field(default="127.0.0.1", alias="DB_HOST")
    db_port: int = Field(default=3306, alias="DB_PORT")
    db_name: str = Field(default="ard_suite_dev", alias="DB_NAME")
    db_user: str = Field(default="ard_suite", alias="DB_USER")
    db_password: str = Field(default="", alias="DB_PASSWORD")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def sqlalchemy_database_uri(self) -> str:
        user = quote_plus(self.db_user)
        password = quote_plus(self.db_password)
        host = self.db_host
        database = quote_plus(self.db_name)
        return f"mysql+pymysql://{user}:{password}@{host}:{self.db_port}/{database}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
