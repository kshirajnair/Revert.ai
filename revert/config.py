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
    )
