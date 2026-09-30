import os
import tempfile
from pathlib import Path

import pytest

ARTIFACTS = Path(os.environ.get("RELIABILITYML_TEST_ARTIFACTS", tempfile.mkdtemp(prefix="rml-artifacts-")))
os.environ["RELIABILITYML_ARTIFACTS"] = str(ARTIFACTS)
os.environ["RELIABILITYML_MLFLOW"] = "0"
os.environ.pop("RELIABILITYML_ANTHROPIC_API_KEY", None)


@pytest.fixture(scope="session")
def artifacts() -> Path:
    """Run the full pipeline once per test session (about a minute)."""
    if not (ARTIFACTS / "summary.json").exists():
        from reliabilityml.pipelines.build import run

        run(ARTIFACTS)
    return ARTIFACTS


@pytest.fixture(scope="session")
def client(artifacts):
    from fastapi.testclient import TestClient

    from reliabilityml.api.main import app

    with TestClient(app) as c:
        yield c
