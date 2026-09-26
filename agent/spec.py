"""Turn the extraction model's raw output into a validated LLD spec, or clarification questions.

The model only transcribes what the customer wrote (values with their units, words as written).
Everything else happens here in code: unit conversion, synonyms, shop rules, defaults, required fields.
"""

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import jsonschema

SPEC_SCHEMA = json.loads((Path(__file__).parent / "schemas" / "spec.schema.json").read_text())

# What the extraction agent must return. Every key is required and nullable so a strict
# JSON-schema response format can express "the customer did not say".
_MEASURE = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["value", "unit"],
    "properties": {"value": {"type": ["number", "null"]}, "unit": {"type": ["string", "null"]}},
}
EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["customer", "items", "target_price"],
    "properties": {
        "customer": {"type": ["string", "null"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "material", "length", "width", "thickness", "qty", "bends", "holes", "weld_length", "finish"],
                "properties": {
                    "name": {"type": ["string", "null"]},
                    "material": {"type": ["string", "null"]},
                    "length": _MEASURE,
                    "width": _MEASURE,
                    "thickness": _MEASURE,
                    "qty": {"type": ["number", "null"]},
                    "bends": {"type": ["number", "null"]},
                    "holes": {"type": ["number", "null"]},
                    "weld_length": _MEASURE,
                    "finish": {"type": ["string", "null"]},
                },
            },
        },
        "target_price": {"type": ["number", "null"]},
    },
}

MM_PER_UNIT = {"mm": 1, "millimetre": 1, "millimeter": 1, "cm": 10, "centimetre": 10, "centimeter": 10,
               "m": 1000, "metre": 1000, "meter": 1000, "in": Decimal("25.4"), "inch": Decimal("25.4"), '"': Decimal("25.4"),
               "ft": Decimal("304.8"), "foot": Decimal("304.8"), "feet": Decimal("304.8")}
MATERIALS = {"ms": "MS", "mild steel": "MS", "m.s.": "MS", "ss304": "SS304", "ss 304": "SS304", "stainless steel 304": "SS304",
             "stainless 304": "SS304", "al": "AL", "aluminium": "AL", "aluminum": "AL"}
# Bare "SS" / "stainless" could be any grade; the grade changes the price, so ask.
AMBIGUOUS_MATERIALS = {"ss", "stainless", "stainless steel", "steel"}
FINISHES = {"powder coat": "powder_coat", "powder coating": "powder_coat", "powder coated": "powder_coat", "powder_coat": "powder_coat",
            "paint": "paint", "painted": "paint", "painting": "paint",
            "galvanise": "galvanise", "galvanised": "galvanise", "galvanize": "galvanise", "galvanized": "galvanise", "galvanising": "galvanise",
            "none": "none", "no finish": "none", "plain": "none", "raw": "none"}

# Shop rules: part name -> ops the shop assumes unless the customer says otherwise. Extend freely.
SHOP_RULES = {
    "l bracket": {"bends": 1},
    "l-bracket": {"bends": 1},
    "angle bracket": {"bends": 1},
    "z bracket": {"bends": 2},
    "u bracket": {"bends": 2},
    "u channel": {"bends": 2},
}

DIM_WORDS = {"length": "length", "width": "width", "thickness": "plate thickness"}

QUESTIONS = {
    "no_items": "Could you please tell us which parts you need, with the material, size, thickness and quantity?",
    "unreadable": "Sorry, we could not read the part details clearly. Could you please share the material, size, thickness and quantity again?",
    "material_missing": "Which material would you like for the {item}: mild steel (MS), stainless steel 304 or aluminium?",
    "material_ambiguous": "Could you please confirm the exact material for the {item}? We stock mild steel (MS), stainless steel 304 and aluminium.",
    "material_unsupported":"We work with mild steel (MS), stainless steel 304 and aluminium. Would one of these suit the {item} instead of {value}?",
    "dim_missing": "Could you please confirm the {dim} of the {item} (in mm)?",
    "dim_invalid": "The {dim} of the {item} came through as {value}, which does not look right. Could you please confirm it?",
    "unit_unknown": "Could you please confirm the unit for the {dim} of the {item} ({value} {unit}), for example mm or cm?",
    "qty_missing": "How many pieces of the {item} do you need?",
    "qty_invalid": "Could you please confirm the quantity of the {item}? It came through as {value}.",
    "finish_unknown": "Could you please confirm the finish for the {item}: powder coating, paint, galvanising or no finish?",
}


@dataclass
class Result:
    spec: dict
    questions: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.spec["missing"]


def _key(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _number(x: Decimal) -> int | float:
    return int(x) if x == x.to_integral_value() else float(x)


def _to_mm(measure: dict | None) -> tuple[str, Decimal | str | None]:
    """Return ("ok", mm) or an error kind with the offending text."""
    if not measure or measure.get("value") is None:
        return "missing", None
    value = Decimal(str(measure["value"]))
    unit = _key(measure.get("unit")) or "mm"  # a bare number on a fabrication drawing is mm
    if unit not in MM_PER_UNIT:
        return "unit_unknown", f"{measure['value']} {measure.get('unit')}"
    if value <= 0:
        return "dim_invalid", f"{measure['value']} {measure.get('unit') or 'mm'}"
    return "ok", value * MM_PER_UNIT[unit]


def _count(value, default: int) -> int:
    return default if value is None else int(value)


def validate(raw: str | dict) -> Result:
    """Validate extraction output. Never raises on bad model output; bad input becomes a question."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        return Result({"customer": None, "items": [], "target_price": None, "missing": ["spec"]}, [QUESTIONS["unreadable"]])

    missing, questions, assumptions, items = [], [], [], []
    if not raw["items"]:
        missing.append("items")
        questions.append(QUESTIONS["no_items"])

    for i, it in enumerate(raw["items"]):
        name = (it.get("name") or "").strip() or f"part {i + 1}"
        path = f"items[{i}]"
        item = {"name": name}

        material = MATERIALS.get(_key(it.get("material")))
        if not it.get("material"):
            missing.append(f"{path}.material")
            questions.append(QUESTIONS["material_missing"].format(item=name))
        elif material is None:
            missing.append(f"{path}.material")
            kind = "material_ambiguous" if _key(it["material"]) in AMBIGUOUS_MATERIALS else "material_unsupported"
            questions.append(QUESTIONS[kind].format(item=name, value=it["material"]))
        item["material"] = material

        for dim in ("length", "width", "thickness"):
            status, value = _to_mm(it.get(dim))
            if status == "ok":
                item[f"{dim}_mm"] = _number(value)
                continue
            item[f"{dim}_mm"] = None
            missing.append(f"{path}.{dim}_mm")
            if status == "missing":
                questions.append(QUESTIONS["dim_missing"].format(dim=DIM_WORDS[dim], item=name))
            elif status == "unit_unknown":
                value_text, unit_text = value.rsplit(" ", 1)
                questions.append(QUESTIONS["unit_unknown"].format(dim=DIM_WORDS[dim], item=name, value=value_text, unit=unit_text))
            else:
                questions.append(QUESTIONS["dim_invalid"].format(dim=DIM_WORDS[dim], item=name, value=value))

        qty = it.get("qty")
        if qty is None:
            missing.append(f"{path}.qty")
            questions.append(QUESTIONS["qty_missing"].format(item=name))
            item["qty"] = None
        elif qty <= 0 or qty != int(qty):
            missing.append(f"{path}.qty")
            questions.append(QUESTIONS["qty_invalid"].format(item=name, value=qty))
            item["qty"] = None
        else:
            item["qty"] = int(qty)

        rule = SHOP_RULES.get(_key(name)) or SHOP_RULES.get(_key(name).removesuffix("s"), {})
        bends = it.get("bends")
        if bends is None and "bends" in rule:
            bends = rule["bends"]
            assumptions.append(f"{name}: {bends} bend(s) per piece (shop default for this part), since the enquiry did not say.")
        weld_status, weld_mm = _to_mm(it.get("weld_length"))
        item["ops"] = {
            "cutting": 1,
            "bends": _count(bends, 0),
            "weld_m": _number(weld_mm / 1000) if weld_status == "ok" else 0,
            "holes": _count(it.get("holes"), 0),
        }
        assumptions.append(f"{name}: 1 cutting operation per piece.")

        finish_text = it.get("finish")
        if not finish_text:
            item["finish"] = "none"
            assumptions.append(f"{name}: no finish, since the enquiry did not mention one.")
        elif FINISHES.get(_key(finish_text)) is None:
            item["finish"] = None
            missing.append(f"{path}.finish")
            questions.append(QUESTIONS["finish_unknown"].format(item=name))
        else:
            item["finish"] = FINISHES[_key(finish_text)]
        items.append(item)

    target = raw.get("target_price")
    spec = {"customer": raw.get("customer"), "items": items, "target_price": target if target and target > 0 else None, "missing": missing}
    if not missing:
        jsonschema.validate(spec, SPEC_SCHEMA)  # our own output must meet the contract; a failure here is a code bug
    return Result(spec, questions, assumptions)
