CREATE TABLE materials (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    density_kg_m3 NUMERIC NOT NULL,
    rate_per_kg NUMERIC NOT NULL,
    wastage_pct NUMERIC NOT NULL
);

CREATE TABLE labour_rates (
    id SERIAL PRIMARY KEY,
    op VARCHAR(50) NOT NULL,
    unit VARCHAR(50) NOT NULL,
    rate NUMERIC NOT NULL
);

CREATE TABLE finishing_rates (
    id SERIAL PRIMARY KEY,
    type VARCHAR(50) NOT NULL,
    rate_per_m2 NUMERIC NOT NULL
);

CREATE TABLE settings (
    id SERIAL PRIMARY KEY,
    overhead_pct NUMERIC NOT NULL,
    margin_pct NUMERIC NOT NULL,
    margin_floor_pct NUMERIC NOT NULL,
    gst_pct NUMERIC NOT NULL
);

CREATE TABLE stock (
    id SERIAL PRIMARY KEY,
    material_id INTEGER REFERENCES materials(id),
    thickness_mm NUMERIC NOT NULL,
    sheet_size VARCHAR(50),
    qty_kg NUMERIC NOT NULL
);

CREATE TABLE quotes (
    id SERIAL PRIMARY KEY,
    customer VARCHAR(100) NOT NULL,
    spec_json JSONB,
    breakdown_json JSONB,
    total NUMERIC,
    status VARCHAR(50) DEFAULT 'draft',
    pdf_path VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
