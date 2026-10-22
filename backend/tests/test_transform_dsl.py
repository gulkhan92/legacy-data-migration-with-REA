from decimal import Decimal

import pandas as pd
import pytest

from app.transform.dsl import RuleError, apply_steps, parse_steps


def run(steps, values, *more):  # type: ignore[no-untyped-def]
    inputs = [pd.Series(values, dtype=object)] + [pd.Series(m, dtype=object) for m in more]
    return apply_steps(parse_steps(steps, len(inputs)), inputs)


def test_decimal_formats_currency_and_parentheses() -> None:
    out = run([{"op": "cast_decimal"}], ["$1,234.50", "12", "(3.5)", "abc", None])
    assert out.values.tolist()[:3] == [Decimal("1234.50"), Decimal("12.00"), Decimal("-3.50")]
    assert out.failed.tolist() == [False, False, False, True, False]
    assert out.kind[3] == "invalid_number"


def test_dates_in_three_formats_and_bad_values() -> None:
    out = run([{"op": "parse_date", "formats": ["%Y-%m-%d", "%d/%m/%Y", "%b %d, %Y"]}], ["2020-01-31", "31/01/2020", "Jan 31, 2020", "31.31.2020"])
    assert [str(v) for v in out.values[:3]] == ["2020-01-31"] * 3
    assert out.failed.tolist() == [False, False, False, True] and out.kind[3] == "invalid_date"


def test_epoch_days_with_null_and_invalid_markers() -> None:
    out = run([{"op": "epoch_days_to_date"}], [40845, 0, -1])
    assert str(out.values[0]) == "2011-10-31" and pd.isna(out.values[1]) and out.failed.tolist() == [False, False, True]


def test_map_codes_fails_unknown_codes_unless_passthrough() -> None:
    strict = run([{"op": "map_codes", "mapping": {"1": "completed"}}], ["1", "9"])
    assert strict.values[0] == "completed" and strict.failed.tolist() == [False, True] and strict.kind[1] == "invalid_code"
    loose = run([{"op": "map_codes", "mapping": {"1": "completed"}, "passthrough": True}], ["1", "9"])
    assert loose.values.tolist() == ["completed", "9"] and not loose.failed.any()


def test_concat_and_bit_flag() -> None:
    assert run([{"op": "concat", "separator": " "}], ["Ada", None], ["Lovelace", "Smith"]).values.tolist() == ["Ada Lovelace", "Smith"]
    flag = run([{"op": "bit_flag", "bit": 0, "true_value": "male", "false_value": "female"}], [1, 2, 3])
    assert flag.values.tolist() == ["male", "female", "male"]


def test_case_fix_only_touches_anomalies() -> None:
    out = run([{"op": "case", "mode": "title", "styles": ["upper", "lower", "inverted"]}], ["BERLIN", "McDonald", "sMITH", "USA", "Paris"])
    assert out.values.tolist() == ["Berlin", "McDonald", "Smith", "USA", "Paris"]


@pytest.mark.parametrize(
    "steps",
    [
        [{"op": "eval", "code": "1+1"}],
        [{"op": "trim", "shell": "rm -rf /"}],
        [{"op": "regex_extract", "pattern": "(a+)+$"}],
        [{"op": "regex_extract", "pattern": r"(a)\1"}],
        [{"op": "parse_date", "formats": ["__import__('os')"]}],
        [],
        [{"op": "trim"}] * 13,
        [{"not_an_op": 1}],
    ],
)
def test_unsafe_or_invalid_rules_are_rejected(steps) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(RuleError):
        parse_steps(steps, 1)
