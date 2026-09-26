---
name: quoteforge-costing
description: Compute fabrication quote weights, stock needs and the full price breakdown (material, labour, finishing, overhead, margin, GST) with a tested script. Use for every quote number.
---

# QuoteForge costing

All quote numbers come from `costing.py` in this skill's directory. It is standard-library Python 3 and prints JSON.

## Input

Write one JSON file with the spec and the rate card exactly as `get_rate_card` returned it:

```json
{"spec": { ...spec JSON... }, "rate_card": { ...get_rate_card output... }}
```

`examples/scenario1.json` is a complete example.

## Steps

1. Weights and stock need, before `check_stock`:
   `python3 costing.py --weight-only < input.json`
   Use each item's `kg_needed` (includes wastage) as `kg_needed` for `check_stock`.
2. Full breakdown:
   `python3 costing.py < input.json`
3. Read `self_check`. Exit code 1 means it failed; `self_check.errors` says why.
4. Read `margin_check`. `below_floor: true` means the owner must approve the margin.
5. Only after the owner approves a below-floor margin, price the quote at the customer's target:
   `python3 costing.py --price-at-target < input.json`
   The margin is solved so `total` equals `target_price` exactly; `priced_at_target` is true and `margin.pct` is the effective margin.

## Negotiation

- Floor price (lowest the shop accepts without owner approval): `python3 costing.py --at-floor < input.json`. `priced_at_floor` is true and the margin is the rate card's `margin_floor_pct`.
- To compare counter-offer options, write your own script that builds one input per option (a changed spec), runs `costing.py --at-floor` on each with `subprocess`, and tabulates `total` and `unit_price_before_gst`. The prices must come from those runs; the script only arranges them.
- Options may change finish or quantity. Never change material or thickness unless the customer asked.

## Notes

- `target_price` in the spec is the customer's order total including GST, or null.
- `--wastage-mode nesting` prices material from whole sheets instead of flat wastage. Default is flat; use it unless told otherwise.
- `nesting.py <length_mm> <width_mm> <qty>` shows sheet layout on its own.
