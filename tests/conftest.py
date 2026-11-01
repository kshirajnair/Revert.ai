import os
from datetime import date

os.environ["LLM_PROVIDER"] = "rules"  # tests never call a real LLM

import pytest

from revert.connectors.mock_tally import MockTallyConnector
from revert.core.db import init_db, make_engine, make_session_factory
from revert.seed.demo_company import build_dataset

AS_OF = date(2026, 10, 30)


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    init_db(engine)
    s = make_session_factory(engine)()
    yield s
    s.close()


@pytest.fixture
def dataset():
    return build_dataset(AS_OF)


@pytest.fixture
def connector(dataset):
    """Full XML round trip, no HTTP."""
    return MockTallyConnector(dataset=dataset, today=AS_OF)
