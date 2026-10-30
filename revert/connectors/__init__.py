"""Accounting-system connectors. All are read-only."""
from __future__ import annotations

from revert.config import Settings
from revert.connectors.base import Connector


def get_connector(settings: Settings, offline: bool = False) -> Connector:
    """Build the connector named by CONNECTOR. `offline` serves the demo books in-process (mock only)."""
    if settings.connector == "mock_tally":
        from revert.connectors.mock_tally import MockTallyConnector

        if offline:
            from revert.core.timeutil import today_ist
            from revert.seed.demo_company import build_dataset

            return MockTallyConnector(dataset=build_dataset(today_ist()))
        return MockTallyConnector(settings.tally_url, settings.tally_company, settings.tally_timeout_s)
    if settings.connector == "tally_xml":
        from revert.connectors.tally_xml import TallyXmlConnector

        return TallyXmlConnector(settings.tally_url, settings.tally_company, settings.tally_timeout_s)
    if settings.connector == "csv":
        raise NotImplementedError("CSV upload connector arrives in Phase 5")
    raise ValueError(f"Unknown CONNECTOR={settings.connector!r}")
