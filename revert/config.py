"""Runtime settings, read from environment / .env. No secrets in code."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str
    # mock_tally | tally_xml | csv  (tally_xml and csv land in Phase 5)
    connector: str
    tally_url: str
    tally_company: str
    tally_timeout_s: float
    mock_tally_host: str
    mock_tally_port: int
    # web_simulator | whatsapp (Phase 4)
    channel: str = "web_simulator"
    host: str = "127.0.0.1"
    port: int = 8000
    # background jobs inside `python -m revert serve`; 0 turns a job off
    sync_every_min: int = 15
    remind_every_min: int = 15
    # gemini | claude | openai | rules. Without the matching key the agent uses rules (offline).
    llm_provider: str = "gemini"
    llm_model: str = ""  # blank = provider default
    llm_timeout_s: float = 20
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    @property
    def llm_api_key(self) -> str:
        return {"gemini": self.gemini_api_key, "claude": self.anthropic_api_key,
                "openai": self.openai_api_key}.get(self.llm_provider.lower(), "")


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./revert.db"),
        connector=os.getenv("CONNECTOR", "mock_tally"),
        tally_url=os.getenv("TALLY_URL", "http://localhost:9000"),
        tally_company=os.getenv("TALLY_COMPANY", ""),
        tally_timeout_s=float(os.getenv("TALLY_TIMEOUT_S", "15")),
        mock_tally_host=os.getenv("MOCK_TALLY_HOST", "127.0.0.1"),
        mock_tally_port=int(os.getenv("MOCK_TALLY_PORT", "9000")),
        channel=os.getenv("CHANNEL", "web_simulator"),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        sync_every_min=int(os.getenv("SYNC_EVERY_MIN", "15")),
        remind_every_min=int(os.getenv("REMIND_EVERY_MIN", "15")),
        llm_provider=os.getenv("LLM_PROVIDER", "gemini"),
        llm_model=os.getenv("LLM_MODEL", ""),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "20")),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
    )
