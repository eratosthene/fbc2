from fbc.util import fpercent, fprice


def test_fprice_formats_integer_as_currency():
    assert fprice(5) == "$5.00"


def test_fprice_rounds_to_two_decimal_places():
    assert fprice(19.999) == "$20.00"


def test_fprice_keeps_existing_decimals():
    assert fprice(12.3) == "$12.30"


def test_fprice_handles_negative_values():
    assert fprice(-3.5) == "$-3.50"


def test_fpercent_converts_fraction_to_percent_string():
    assert fpercent(0.5) == "50.00%"


def test_fpercent_handles_zero():
    assert fpercent(0) == "0.00%"


def test_fpercent_handles_values_over_one():
    assert fpercent(1.2345) == "123.45%"
