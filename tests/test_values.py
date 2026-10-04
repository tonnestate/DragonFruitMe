import pytest

from dragonfruitme.values import equivalent, find_typed, fold, parse_number, tokenize, validate


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("349.000 €", 349000),
        ("349.000,50 €", 349000.5),
        ("€ 1.250.000", 1250000),
        ("1,200,000 USD", 1200000),
        ("1,200.50", 1200.5),
        ("78,5 m²", 78.5),
        ("72.5", 72.5),
        ("3", 3),
        ("-12,5", -12.5),
        ("keine Zahl", None),
    ],
)
def test_parse_number_german_and_english(raw, expected):
    assert parse_number(raw) == expected


def test_fold_and_tokenize_handle_umlauts():
    assert fold("Wohnfläche  GRÖSSE ß") == "wohnflaeche groesse ss"
    assert tokenize("Energieträger: Gas") == ["energietraeger", "gas"]


def test_validate_types_and_bounds():
    assert validate("349.000 €", {"type": "price"}) == (True, 349000.0, "OK")
    assert validate("349000", {"type": "price"})[2] == "TYPE_MISMATCH"
    assert validate("349000", {"type": "price"}, typed_source=True)[:2] == (True, 349000.0)
    assert validate("78,5 m²", {"type": "area"})[1] == 78.5
    assert validate("1998", {"type": "integer", "min": 1800, "max": 2030}) == (True, 1998, "OK")
    assert validate("1700", {"type": "integer", "min": 1800})[2] == "BELOW_MIN"
    assert validate("max@example.de", {"type": "email"})[1] == "max@example.de"
    assert validate("", {"type": "text"})[2] == "EMPTY"
    assert validate("ABC-123", {"type": "text", "pattern": r"^[A-Z]{3}-\d+$"})[0]
    assert validate("abc", {"type": "text", "pattern": r"^\d+$"})[2] == "PATTERN_MISMATCH"


def test_find_typed_and_equivalence():
    assert find_typed("Preis ab 1.250.000 € zzgl. Provision", "price") == "1.250.000 €"
    assert find_typed("Fläche ca. 112 qm", "area") == "112 qm"
    assert equivalent("349.000 €", "349000 EUR", "price")
    assert equivalent("Gas", "gas", "text")
    assert not equivalent("Gas", "Öl", "text")
