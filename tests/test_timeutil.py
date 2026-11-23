import pytest

from reviewpulse.data.timeutil import infer_timestamp_unit


def test_infer_ms_and_s():
    assert infer_timestamp_unit([1667469600000, 1682330400000]) == "ms"
    assert infer_timestamp_unit([1667469600, 1682330400]) == "s"


@pytest.mark.parametrize(
    "values",
    [
        [],
        [1667469600, 1667469600000],  # mixed units
        [16674696000],  # 11 digits: ambiguous
        [-1],
        ["1667469600"],
        [True],
    ],
)
def test_infer_rejects_ambiguous(values):
    with pytest.raises(ValueError):
        infer_timestamp_unit(values)
