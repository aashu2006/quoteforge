const express = require('express');
const { execSync } = require('child_process');
const { writeFileSync, unlinkSync } = require('fs');
const path = require('path');
const os = require('os');
const { TrueForgeHarness } = require('./agent/harness');
const { getRateCard } = require('./tools/rate_card');
const { checkStock } = require('./tools/stock');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'ui')));

// ── POST /api/quote ───────────────────────────────────────────────────────────
// Main endpoint: runs the full agent loop on a customer enquiry
app.post('/api/quote', async (req, res) => {
    const { enquiry } = req.body;
    if (!enquiry) return res.status(400).json({ error: 'No enquiry provided' });

    try {
        const tmpFile = path.join(os.tmpdir(), `qf_enquiry_${Date.now()}.txt`);
        writeFileSync(tmpFile, enquiry, 'utf8');

        // We use uv run to execute the query_agent.py script inside the agent dir
        const output = execSync(
            `uv run query_agent.py < "${tmpFile}"`,
            { cwd: path.join(__dirname, 'agent') }
        ).toString();
        
        unlinkSync(tmpFile);
        
        const result = JSON.parse(output);
        res.json(result);
    } catch (err) {
        console.error(err);
        let logs = [];
        if (err.stdout) logs.push(err.stdout.toString());
        if (err.stderr) logs.push(err.stderr.toString());
        res.status(500).json({ error: err.message, logs: logs });
    }
});

// ── POST /api/rates ───────────────────────────────────────────────────────────
app.get('/api/rates', async (req, res) => {
    try {
        const rates = await getRateCard();
        res.json(rates);
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

// ── POST /api/stock ───────────────────────────────────────────────────────────
app.get('/api/stock/:material/:thickness/:kgNeeded', async (req, res) => {
    const { material, thickness, kgNeeded } = req.params;
    try {
        const result = await checkStock(material, parseFloat(thickness), parseFloat(kgNeeded));
        res.json(result);
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`\n🚀 QuoteForge server running at http://localhost:${PORT}`);
    console.log(`   Open http://localhost:${PORT} in your browser\n`);
});
