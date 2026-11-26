import pytest

from rigpulse.detection import detect
from rigpulse.ingest import ingest_csv
from rigpulse.simulate import generate


@pytest.fixture
def populated(tmp_path):
    db = str(tmp_path / "test.db")
    generated = generate(tmp_path / "data", rigs=3, hours=100, seed=42)
    loaded = ingest_csv(generated["telemetry_path"], db)
    detected = detect(db)
    return db, generated, loaded, detected
