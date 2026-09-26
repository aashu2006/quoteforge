"""QuoteForge costing: spec JSON + rate card JSON -> quote breakdown JSON. Standard library only.

Run: python3 costing.py < input.json                 full breakdown
     python3 costing.py --weight-only < input.json   weights and kg_needed for the stock check
     python3 costing.py --wastage-mode nesting ...   material from whole sheets instead of flat wastage
     python3 costing.py --price-at-target ...        margin solved so the total equals target_price
     python3 costing.py --at-floor ...               margin at the rate card's margin floor (lowest acceptable price)
Input: {"spec": <spec JSON>, "rate_card": <get_rate_card output>}
Exit code 1 when the self-check fails; the JSON still prints with the reasons.
"""

import argparse
import json
import sys
from decimal import ROUND_HALF_UP, Decimal

from nesting import SHEET_L_MM, SHEET_W_MM, nest

PAISE = Decimal("0.01")
MIN_PIECE_KG = Decimal("0.001")
MAX_PIECE_KG = Decimal("2000")

# Spec ops key -> (labour op in the rate card, label)
OPS = {"cutting": ("cutting", "Cutting"), "bends": ("bending", "Bending"), "weld_m": ("welding", "Welding"), "holes": ("drilling", "Drilling")}


class InputError(ValueError):
    pass


def money(x: Decimal) -> Decimal:
    return x.quantize(PAISE, rounding=ROUND_HALF_UP)


def dec(x) -> Decimal:
    return Decimal(str(x))


def pct(x) -> Decimal:
    return dec(x) / 100


def num(x: Decimal) -> float | int:
    return int(x) if x == x.to_integral_value() else float(x)


def index_rates(rate_card: dict) -> tuple[dict, dict, dict, dict]:
    materials = {m["name"]: m for m in rate_card["materials"]}
    labour = {r["op"]: r for r in rate_card.get("labour_rates", [])}
    finishing = {r["type"]: r for r in rate_card.get("finishing_rates", [])}
    return materials, labour, finishing, rate_card["settings"]


def item_weight(item: dict, material: dict, wastage_mode: str) -> dict:
    length, width, thick = dec(item["length_mm"]), dec(item["width_mm"]), dec(item["thickness_mm"])
    qty = dec(item["qty"])
    if min(length, width, thick, qty) <= 0:
        raise InputError(f"{item['name']}: dimensions and qty must be positive")

    density = dec(material["density_kg_m3"])
    piece_kg = length * width * thick * density * Decimal("1e-9")
    total_kg = piece_kg * qty
    layout = nest(float(length), float(width), int(qty))

    if wastage_mode == "nesting":
        sheet_kg = dec(SHEET_W_MM) * dec(SHEET_L_MM) * thick * density * Decimal("1e-9")
        kg_needed = sheet_kg * layout["sheets_needed"]
    else:
        kg_needed = total_kg * (1 + pct(material["wastage_pct"]))
    return {"piece_kg": piece_kg, "total_kg": total_kg, "kg_needed": kg_needed, "nesting": layout}


def cost_item(item: dict, rates: tuple, wastage_mode: str) -> tuple[dict, list[str]]:
    materials, labour, finishing, _ = rates
    material = materials.get(item["material"])
    if material is None:
        raise InputError(f"{item['name']}: no rate for material {item['material']}")

    w = item_weight(item, material, wastage_mode)
    qty = dec(item["qty"])
    rate_kg = dec(material["rate_per_kg"])
    lines = []

    if wastage_mode == "nesting":
        detail = f"{w['nesting']['sheets_needed']} sheet(s), {num(round(w['kg_needed'], 3))} kg {item['material']} @ Rs {num(rate_kg)}/kg"
    else:
        detail = f"{num(round(w['total_kg'], 3))} kg {item['material']} @ Rs {num(rate_kg)}/kg + {num(dec(material['wastage_pct']))}% wastage"
    lines.append({"label": "Material", "detail": detail, "amount": money(w["kg_needed"] * rate_kg)})

    for key, count in (item.get("ops") or {}).items():
        if key not in OPS:
            raise InputError(f"{item['name']}: unknown op {key}")
        count = dec(count)
        if count == 0:
            continue
        op, label = OPS[key]
        if op not in labour:
            raise InputError(f"{item['name']}: no labour rate for {op}")
        rate = dec(labour[op]["rate"])
        lines.append({"label": label, "detail": f"{num(qty)} x {num(count)} @ Rs {num(rate)} {labour[op]['unit']}", "amount": money(count * rate * qty)})

    finish = item.get("finish")
    if finish and finish != "none":
        if finish not in finishing:
            raise InputError(f"{item['name']}: no finishing rate for {finish}")
        area_m2 = 2 * dec(item["length_mm"]) * dec(item["width_mm"]) / Decimal("1e6")  # both faces
        rate = dec(finishing[finish]["rate_per_m2"])
        lines.append({"label": "Finishing", "detail": f"{finish}, {num(area_m2 * qty)} m2 (both faces) @ Rs {num(rate)}/m2", "amount": money(area_m2 * qty * rate)})

    errors = []
    if not MIN_PIECE_KG <= w["piece_kg"] <= MAX_PIECE_KG:
        errors.append(f"{item['name']}: weight {w['piece_kg']:.3f} kg per piece is outside {MIN_PIECE_KG}-{MAX_PIECE_KG} kg")

    return {
        "name": item["name"],
        "material": item["material"],
        "qty": item["qty"],
        "dimensions_mm": [item["length_mm"], item["width_mm"], item["thickness_mm"]],
        "weight_kg_per_piece": w["piece_kg"].quantize(Decimal("0.001")),
        "weight_kg_total": w["total_kg"].quantize(Decimal("0.001")),
        "kg_needed": w["kg_needed"].quantize(Decimal("0.01")),
        "nesting": w["nesting"],
        "line_items": lines,
        "item_cost": sum((line["amount"] for line in lines), Decimal(0)),
    }, errors


def self_check(breakdown: dict) -> list[str]:
    errors = []
    amounts = [("overhead", breakdown["overhead"]["amount"]), ("margin", breakdown["margin"]["amount"]), ("gst", breakdown["gst"]["amount"])]
    for item in breakdown["items"]:
        amounts += [(f"{item['name']} {line['label']}", line["amount"]) for line in item["line_items"]]
        if item["item_cost"] != sum((line["amount"] for line in item["line_items"]), Decimal(0)):
            errors.append(f"{item['name']}: item cost does not equal its line items")
    errors += [f"{label} is negative ({amount})" for label, amount in amounts if amount < 0]

    if breakdown["total"] <= 0:
        errors.append("total must be greater than 0")
    if breakdown["cost_subtotal"] != sum((i["item_cost"] for i in breakdown["items"]), Decimal(0)):
        errors.append("cost subtotal does not equal the sum of item costs")
    if breakdown["price_before_gst"] != breakdown["cost_subtotal"] + breakdown["overhead"]["amount"] + breakdown["margin"]["amount"]:
        errors.append("price before GST does not equal cost + overhead + margin")
    if breakdown["total"] != breakdown["price_before_gst"] + breakdown["gst"]["amount"]:
        errors.append("total does not equal price before GST + GST")
    return errors


def split_target(total: Decimal, g: Decimal) -> tuple[Decimal, Decimal]:
    """Split a GST-inclusive total into (price before GST, GST) that sum to it exactly.

    Prefers a split where GST is exactly the rounded rate on the base; if paise rounding makes that
    impossible, GST takes the one-paisa difference.
    """
    base = money(total / (1 + g))
    for candidate in (base, base - PAISE, base + PAISE, base - 2 * PAISE, base + 2 * PAISE):
        if candidate + money(candidate * g) == total:
            return candidate, money(candidate * g)
    return base, total - base


def compute(spec: dict, rate_card: dict, wastage_mode: str = "flat", price_at_target: bool = False, at_floor: bool = False) -> dict:
    if price_at_target and at_floor:
        raise InputError("--price-at-target and --at-floor cannot be combined")
    rates = index_rates(rate_card)
    settings = rates[3]
    items, errors = [], []
    for item in spec["items"]:
        costed, item_errors = cost_item(item, rates, wastage_mode)
        items.append(costed)
        errors += item_errors

    o, m, g, floor = pct(settings["overhead_pct"]), pct(settings["margin_pct"]), pct(settings["gst_pct"]), pct(settings["margin_floor_pct"])
    if at_floor:
        m = floor  # lowest price the shop accepts without owner approval
    subtotal = sum((i["item_cost"] for i in items), Decimal(0))
    overhead = money(subtotal * o)
    cost_with_overhead = subtotal + overhead

    # target_price is the customer's order total including GST.
    target = spec.get("target_price")
    if price_at_target:
        if target is None:
            raise InputError("--price-at-target needs target_price in the spec")
        before_gst, gst = split_target(money(dec(target)), g)
        margin = before_gst - cost_with_overhead  # absorbs rounding so lines sum to the target exactly
        margin_pct = (margin / cost_with_overhead * 100).quantize(PAISE, rounding=ROUND_HALF_UP)
    else:
        margin = money(cost_with_overhead * m)
        margin_pct = settings["margin_floor_pct"] if at_floor else settings["margin_pct"]
        before_gst = cost_with_overhead + margin
        gst = money(before_gst * g)
    for item in items:
        item["unit_price_before_gst"] = money(item["item_cost"] * (1 + o) * (1 + margin / cost_with_overhead) / dec(item["qty"]))

    effective = None
    if target is not None:
        effective = (dec(target) / (1 + g) / cost_with_overhead - 1) * 100
    below_floor = m < floor or (effective is not None and effective < floor * 100)

    breakdown = {
        "customer": spec.get("customer"),
        "wastage_mode": wastage_mode,
        "priced_at_target": price_at_target,
        "priced_at_floor": at_floor,
        "items": items,
        "cost_subtotal": subtotal,
        "overhead": {"pct": settings["overhead_pct"], "amount": overhead},
        "margin": {"pct": margin_pct, "amount": margin},
        "price_before_gst": before_gst,
        "gst": {"pct": settings["gst_pct"], "amount": gst},
        "total": before_gst + gst,
        "margin_check": {
            "margin_floor_pct": settings["margin_floor_pct"],
            "target_price": target,
            "effective_margin_pct": None if effective is None else effective.quantize(PAISE, rounding=ROUND_HALF_UP),
            "below_floor": below_floor,
        },
    }
    errors += self_check(breakdown)
    breakdown["self_check"] = {"passed": not errors, "errors": errors}
    return breakdown


def weights(spec: dict, rate_card: dict, wastage_mode: str = "flat") -> dict:
    materials = index_rates(rate_card)[0]
    out = []
    for item in spec["items"]:
        if item["material"] not in materials:
            raise InputError(f"{item['name']}: no rate for material {item['material']}")
        w = item_weight(item, materials[item["material"]], wastage_mode)
        out.append({
            "name": item["name"],
            "material": item["material"],
            "thickness_mm": item["thickness_mm"],
            "weight_kg_per_piece": w["piece_kg"].quantize(Decimal("0.001")),
            "weight_kg_total": w["total_kg"].quantize(Decimal("0.001")),
            "kg_needed": w["kg_needed"].quantize(Decimal("0.01")),
            "nesting": w["nesting"],
        })
    return {"wastage_mode": wastage_mode, "items": out}


def to_json(obj) -> str:
    return json.dumps(obj, indent=2, default=lambda d: num(d) if isinstance(d, Decimal) else str(d))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weight-only", action="store_true")
    parser.add_argument("--wastage-mode", choices=["flat", "nesting"], default="flat")
    parser.add_argument("--price-at-target", action="store_true", help="solve margin so the total equals target_price")
    parser.add_argument("--at-floor", action="store_true", help="price at the margin floor from the rate card")
    args = parser.parse_args()
    data = json.load(sys.stdin)
    try:
        if args.weight_only:
            print(to_json(weights(data["spec"], data["rate_card"], args.wastage_mode)))
            return 0
        breakdown = compute(data["spec"], data["rate_card"], args.wastage_mode, args.price_at_target, args.at_floor)
    except (InputError, KeyError, ValueError) as e:
        print(to_json({"self_check": {"passed": False, "errors": [f"invalid input: {e}"]}}))
        return 1
    print(to_json(breakdown))
    return 0 if breakdown["self_check"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
