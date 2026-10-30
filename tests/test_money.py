import pytest

from revert.core.money import format_inr, rupees_to_paise
from revert.core.phones import normalize_phone


@pytest.mark.parametrize("paise,expected", [
    (0, "₹0"), (99_900, "₹999"), (1_000_00, "₹1,000"), (1_23_456_00, "₹1,23,456"),
    (1_00_00_000_00, "₹1,00,00,000"), (-45_000_00, "-₹45,000"), (12_345_50, "₹12,346"),
])
def test_format_inr(paise, expected):
    assert format_inr(paise) == expected


def test_format_inr_with_paise():
    assert format_inr(2_93_58_40, show_paise=True) == "₹29,358.40"


def test_rupees_to_paise_exact():
    assert rupees_to_paise("-27848.00") == -2_784_800
    assert rupees_to_paise("0.10") + rupees_to_paise("0.20") == 30  # no float drift
    assert rupees_to_paise("1,180.005") == 118_001


@pytest.mark.parametrize("raw,expected", [
    ("98450 12345", "+919845012345"), ("+91-98450-12345", "+919845012345"),
    ("09845012345", "+919845012345"), ("", None), (None, None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected
