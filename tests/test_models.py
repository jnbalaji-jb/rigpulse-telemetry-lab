import pytest
from rigpulse.models import Reading, normalize_timestamp


def test_timezone_conversion():
    assert normalize_timestamp("2026-01-01T05:30:00+05:30") == "2026-01-01T00:00:00Z"


def test_naive_datetime_rejected():
    with pytest.raises(ValueError, match="timezone"):
        normalize_timestamp("2026-01-01T01:00:00")


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "not-a-number"])
def test_non_finite_or_invalid_rejected(value):
    with pytest.raises(ValueError):
        Reading.parse({"observed_at": "2026-01-01T00:00:00Z", "rig_id": "RIG-01",
                       "metric": "temperature_c", "value": value})


def test_unknown_metrics_rejected():
    with pytest.raises(ValueError, match="unknown metric"):
        Reading.parse({"observed_at": "2026-01-01T00:00:00Z", "rig_id": "RIG-01",
                       "metric": "arbitrary", "value": "2"})
