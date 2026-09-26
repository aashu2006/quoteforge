import copy
import json
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))

from spec import validate  # noqa: E402

S1_SPEC = json.loads((AGENT.parent / "sandbox" / "examples" / "scenario1.json").read_text())["spec"]

S1_RAW = {
    "customer": "Sharma Industries",
    "items": [{
        "name": "L bracket", "material": "MS",
        "length": {"value": 200, "unit": "mm"}, "width": {"value": 100, "unit": "mm"}, "thickness": {"value": 8, "unit": "mm"},
        "qty": 50, "bends": 1, "holes": 2, "weld_length": None, "finish": "powder coated",
    }],
    "target_price": None,
}


def raw(**changes):
    r = copy.deepcopy(S1_RAW)
    r["items"][0].update(changes)
    return r


def test_scenario1_matches_lld_spec():
    result = validate(S1_RAW)
    assert result.ok and result.questions == []
    assert result.spec == S1_SPEC


def test_scenario2_missing_thickness_asks_one_question():
    result = validate(raw(thickness=None))
    assert not result.ok
    assert result.spec["missing"] == ["items[0].thickness_mm"]
    assert result.questions == ["Could you please confirm the plate thickness of the L bracket (in mm)?"]


def test_scenario6_cm_and_shop_rule_give_scenario1_spec():
    result = validate(raw(length={"value": 20, "unit": "cm"}, width={"value": 10, "unit": "cm"}, bends=None, finish="powder coating"))
    assert result.ok
    assert result.spec == S1_SPEC
    assert any("1 bend" in a for a in result.assumptions)


def test_shop_rule_matches_plural_name():
    result = validate(raw(name="L brackets", bends=None))
    assert result.spec["items"][0]["ops"]["bends"] == 1


def test_finish_defaults_to_none_with_assumption():
    result = validate(raw(finish=None))
    assert result.ok and result.spec["items"][0]["finish"] == "none"
    assert any("no finish" in a for a in result.assumptions)


def test_json_string_input_and_parse_failure():
    assert validate(json.dumps(S1_RAW)).ok
    bad = validate("not json {")
    assert not bad.ok and len(bad.questions) == 1


@pytest.mark.parametrize("changes, missing, word", [
    ({"material": "copper"}, "items[0].material", "copper"),
    ({"material": "SS"}, "items[0].material", "exact material"),
    ({"material": None}, "items[0].material", "Which material"),
    ({"length": {"value": -5, "unit": "mm"}}, "items[0].length_mm", "does not look right"),
    ({"width": {"value": 0, "unit": "cm"}}, "items[0].width_mm", "does not look right"),
    ({"thickness": {"value": 8, "unit": "furlong"}}, "items[0].thickness_mm", "unit"),
    ({"qty": None}, "items[0].qty", "How many"),
    ({"qty": 2.5}, "items[0].qty", "quantity"),
    ({"finish": "chrome"}, "items[0].finish", "finish"),
])
def test_invalid_values_become_questions(changes, missing, word):
    result = validate(raw(**changes))
    assert result.spec["missing"] == [missing]
    assert len(result.questions) == 1 and word in result.questions[0]


def test_one_question_per_missing_field():
    result = validate(raw(thickness=None, qty=None, material=None))
    assert len(result.spec["missing"]) == 3 and len(result.questions) == 3


def test_no_items():
    result = validate({"customer": None, "items": [], "target_price": None})
    assert result.spec["missing"] == ["items"] and len(result.questions) == 1


def test_bare_number_means_mm_and_inches_convert():
    result = validate(raw(length={"value": 200, "unit": None}, width={"value": 4, "unit": "inch"}))
    assert result.spec["items"][0]["length_mm"] == 200
    assert result.spec["items"][0]["width_mm"] == 101.6
