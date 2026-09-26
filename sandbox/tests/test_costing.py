import json
import subprocess
import sys
from decimal import Decimal

import pytest

from conftest import SANDBOX
from costing import InputError, compute, self_check, weights


def item(breakdown):
    return breakdown["items"][0]


def test_scenario1_hand_check(scenario1):
    b = compute(scenario1["spec"], scenario1["rate_card"])
    assert item(b)["weight_kg_per_piece"] == Decimal("1.256")
    assert item(b)["weight_kg_total"] == Decimal("62.8")
    material = item(b)["line_items"][0]
    assert material["label"] == "Material"
    assert round(material["amount"]) == 4088
    assert item(b)["nesting"]["parts_per_sheet"] == 150
    assert b["self_check"]["passed"]


def test_scenario1_totals(scenario1):
    b = compute(scenario1["spec"], scenario1["rate_card"])
    # Material 4088.28 + cutting 750 + bending 500 + drilling 500 + powder coat 360
    assert b["cost_subtotal"] == Decimal("6198.28")
    assert b["total"] == Decimal("9654.44")
    assert not b["margin_check"]["below_floor"]


def test_kg_needed_includes_wastage(scenario1):
    w = weights(scenario1["spec"], scenario1["rate_card"])
    assert item(w)["kg_needed"] == Decimal("65.94")


def test_nesting_mode_leaves_flat_hand_check_alone(scenario1):
    nested = compute(scenario1["spec"], scenario1["rate_card"], wastage_mode="nesting")
    # One full 1250 x 2500 x 8 mm MS sheet = 196.25 kg
    assert item(nested)["kg_needed"] == Decimal("196.25")
    flat = compute(scenario1["spec"], scenario1["rate_card"])
    assert round(item(flat)["line_items"][0]["amount"]) == 4088


def test_no_finish_and_no_ops_is_valid(scenario1):
    spec = scenario1["spec"]
    spec["items"][0].update(finish="none", ops={})
    b = compute(spec, scenario1["rate_card"])
    assert [line["label"] for line in item(b)["line_items"]] == ["Material"]
    assert b["self_check"]["passed"]


def test_target_price_below_floor(scenario1):
    scenario1["spec"]["target_price"] = 8700
    check = compute(scenario1["spec"], scenario1["rate_card"])["margin_check"]
    assert check["effective_margin_pct"] == Decimal("8.14")
    assert check["below_floor"]


def test_price_at_target_scenario3(scenario1):
    scenario1["spec"]["target_price"] = 8700
    b = compute(scenario1["spec"], scenario1["rate_card"], price_at_target=True)
    assert b["priced_at_target"]
    assert b["total"] == Decimal("8700.00")
    assert b["margin"]["pct"] == Decimal("8.14")
    assert b["margin_check"]["effective_margin_pct"] == Decimal("8.14")
    assert b["cost_subtotal"] == Decimal("6198.28")  # costs unchanged, only margin moves
    assert b["price_before_gst"] + b["gst"]["amount"] == b["total"]
    assert b["self_check"]["passed"]


def test_price_at_target_sums_exactly_for_any_target(scenario1):
    for paise in range(870000, 870200):
        scenario1["spec"]["target_price"] = paise / 100
        b = compute(scenario1["spec"], scenario1["rate_card"], price_at_target=True)
        assert b["total"] == Decimal(paise) / 100
        assert b["self_check"]["passed"], b["self_check"]["errors"]


def test_price_at_target_below_cost_fails_self_check(scenario1):
    scenario1["spec"]["target_price"] = 5000  # below cost + overhead
    b = compute(scenario1["spec"], scenario1["rate_card"], price_at_target=True)
    assert not b["self_check"]["passed"]
    assert any("margin is negative" in e for e in b["self_check"]["errors"])


def test_price_at_target_needs_target(scenario1):
    with pytest.raises(InputError):
        compute(scenario1["spec"], scenario1["rate_card"], price_at_target=True)


def test_at_floor_scenario1(scenario1):
    b = compute(scenario1["spec"], scenario1["rate_card"], at_floor=True)
    assert b["priced_at_floor"] and not b["priced_at_target"]
    assert b["cost_subtotal"] == Decimal("6198.28")
    assert b["margin"] == {"pct": 12, "amount": Decimal("818.17")}
    assert b["total"] == Decimal("9010.81")
    assert not b["margin_check"]["below_floor"]
    assert b["self_check"]["passed"]


def test_at_floor_negotiation_options(scenario1):
    # Scenario 4 counter-offer options: change finish only, never material or thickness.
    totals = {}
    for finish in ("powder_coat", "paint", "none"):
        scenario1["spec"]["items"][0]["finish"] = finish
        totals[finish] = compute(scenario1["spec"], scenario1["rate_card"], at_floor=True)["total"]
    assert totals["powder_coat"] > totals["paint"] > totals["none"]
    assert totals["none"] > 7500  # no finish option reaches the customer's Rs 7,500


def test_at_floor_leaves_standard_pricing_alone(scenario1):
    compute(scenario1["spec"], scenario1["rate_card"], at_floor=True)
    assert compute(scenario1["spec"], scenario1["rate_card"])["total"] == Decimal("9654.44")


def test_at_floor_and_target_are_exclusive(scenario1):
    scenario1["spec"]["target_price"] = 8700
    with pytest.raises(InputError):
        compute(scenario1["spec"], scenario1["rate_card"], price_at_target=True, at_floor=True)


def test_standard_pricing_not_priced_at_target(scenario1):
    assert compute(scenario1["spec"], scenario1["rate_card"])["priced_at_target"] is False


def test_self_check_catches_bad_sums_and_negatives(scenario1):
    b = compute(scenario1["spec"], scenario1["rate_card"])
    b["items"][0]["line_items"][1]["amount"] = Decimal("-1")
    b["total"] += 1
    errors = self_check(b)
    assert any("negative" in e for e in errors)
    assert any("item cost" in e for e in errors)
    assert any("total does not equal" in e for e in errors)


def test_self_check_flags_insane_weight(scenario1):
    scenario1["spec"]["items"][0].update(length_mm=2000, width_mm=1000, thickness_mm=200)  # 3140 kg per piece
    b = compute(scenario1["spec"], scenario1["rate_card"])
    assert not b["self_check"]["passed"]
    assert any("weight" in e for e in b["self_check"]["errors"])


def test_cli_exit_codes(scenario1):
    ok = subprocess.run([sys.executable, "costing.py"], cwd=SANDBOX, input=json.dumps(scenario1), capture_output=True, text=True)
    assert ok.returncode == 0 and json.loads(ok.stdout)["total"] == 9654.44

    scenario1["spec"]["items"][0]["material"] = "Titanium"
    bad = subprocess.run([sys.executable, "costing.py"], cwd=SANDBOX, input=json.dumps(scenario1), capture_output=True, text=True)
    assert bad.returncode == 1 and not json.loads(bad.stdout)["self_check"]["passed"]
