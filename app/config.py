"""Application settings, loaded from environment variables (or .env locally)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Auth
    session_secret: str = "dev-secret-change-me"
    portal_users: str = ""  # "name:bcrypt-hash,name:bcrypt-hash"

    # Notion
    notion_api_token: str = ""
    notion_meetings_db: str = "2e42d7a24378803fb811d2f6ed029137"
    notion_items_db: str = "2e32d7a2437880298c81f1af94c441a0"
    notion_projects_db: str = "2e42d7a2437880d686e8ff554556b0c1"
    notion_newsletters_db: str = ""

    # AI
    anthropic_api_key: str = ""

    # Sending
    resend_api_key: str = ""
    newsletter_from: str = "Lambeth Cyclists <newsletter@lambethcyclists.com>"
    group_email: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
