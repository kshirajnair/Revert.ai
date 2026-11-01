"""Inbound message handler for the collections vertical: the AI agent in `revert/agent`.

The agent is built once from settings (LLM_PROVIDER + key, or offline rules). Callers such as
the dashboard or tests can pass their own `Agent`.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from revert.agent.agent import Agent, Reply
from revert.agent.llm import get_llm
from revert.config import get_settings
from revert.core.models import Buyer, MessageLog

__all__ = ["Reply", "default_agent", "handle"]


@lru_cache(maxsize=1)
def default_agent() -> Agent:
    s = get_settings()
    return Agent(get_llm(s.llm_provider, s.llm_api_key, s.llm_model, s.llm_timeout_s))


def handle(session, buyer: Buyer, inbound: MessageLog, agent: Agent | None = None,
           now_utc: datetime | None = None) -> Reply:
    return (agent or default_agent()).handle(session, buyer, inbound, now_utc=now_utc)
