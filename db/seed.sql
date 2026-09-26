INSERT INTO materials (name, density_kg_m3, rate_per_kg, wastage_pct) VALUES
('MS', 7850, 62, 0.05),
('SS304', 8000, 230, 0.05),
('AL', 2700, 280, 0.05);

INSERT INTO labour_rates (op, unit, rate) VALUES
('cutting', 'per_piece', 15),
('bending', 'per_bend', 10),
('welding', 'per_m', 120),
('drilling', 'per_hole', 5);

INSERT INTO finishing_rates (type, rate_per_m2) VALUES
('powder_coat', 180),
('paint', 90),
('galvanise', 250);

INSERT INTO settings (overhead_pct, margin_pct, margin_floor_pct, gst_pct) VALUES
(0.10, 0.20, 0.12, 0.18);

INSERT INTO stock (material_id, thickness_mm, sheet_size, qty_kg) VALUES
(1, 8, '1250x2500', 500),
(2, 5, '1250x2500', 20),
(3, 3, '1250x2500', 100);
