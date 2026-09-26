const express = require('express');
const { spawn } = require('child_process');
const path = require('path');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'ui')));
app.use('/quotes', express.static(path.join(__dirname, 'quotes')));

// A cold sandbox plus a full agent turn can take a couple of minutes.
const API_TIMEOUT_MS = Number(process.env.API_TIMEOUT_MS) || 240000;

// Runs agent/api.py <command> with the request as JSON on stdin; resolves with its JSON reply.
function callAgent(command, payload) {
    return new Promise((resolve) => {
        const child = spawn('uv', ['run', 'api.py', command], { cwd: path.join(__dirname, 'agent') });
        let stdout = '';
        let stderr = '';
        const timer = setTimeout(() => {
            child.kill('SIGTERM');
            resolve({ httpStatus: 504, body: { status: 'error', error: `Agent did not answer within ${API_TIMEOUT_MS / 1000}s` } });
        }, API_TIMEOUT_MS);

        child.stdout.on('data', (d) => { stdout += d; });
        child.stderr.on('data', (d) => { stderr += d; });
        child.on('error', (err) => {
            clearTimeout(timer);
            resolve({ httpStatus: 500, body: { status: 'error', error: err.message } });
        });
        child.on('close', () => {
            clearTimeout(timer);
            if (stderr.trim()) console.error(`[api.py ${command}] ${stderr.trim()}`);
            try {
                const body = JSON.parse(stdout);
                resolve({ httpStatus: body.status === 'error' ? 500 : 200, body });
            } catch {
                resolve({ httpStatus: 500, body: { status: 'error', error: 'Agent returned no JSON', logs: [stderr.slice(-2000)] } });
            }
        });
        child.stdin.end(JSON.stringify(payload));
    });
}

// One agent call per session at a time: a double click must not answer the same gate twice.
const busySessions = new Set();
async function callForSession(res, sessionId, command, payload) {
    if (busySessions.has(sessionId)) {
        return res.status(409).json({ status: 'error', error: 'This quote is already being processed. Wait for it to finish.' });
    }
    busySessions.add(sessionId);
    try {
        const { httpStatus, body } = await callAgent(command, payload);
        res.status(httpStatus).json(body);
    } finally {
        busySessions.delete(sessionId);
    }
}

// POST /api/quote {enquiry}: extract, validate, and run the agent to its first approval gate.
app.post('/api/quote', async (req, res) => {
    const { enquiry } = req.body || {};
    if (!enquiry || !enquiry.trim()) return res.status(400).json({ status: 'error', error: 'No enquiry provided' });
    const { httpStatus, body } = await callAgent('start', { enquiry });
    res.status(httpStatus).json(body);
});

// POST /api/decision {session_id, decision: "allow"|"deny", reason?}: answer the pending gate.
app.post('/api/decision', async (req, res) => {
    const { session_id, decision, reason } = req.body || {};
    if (!session_id || !['allow', 'deny'].includes(decision)) {
        return res.status(400).json({ status: 'error', error: 'session_id and decision (allow|deny) are required' });
    }
    await callForSession(res, session_id, 'decide', { session_id, decision, reason });
});

// POST /api/reply {session_id, message}: a customer's reply to a sent quote, into the same session.
app.post('/api/reply', async (req, res) => {
    const { session_id, message } = req.body || {};
    if (!session_id || !message || !message.trim()) {
        return res.status(400).json({ status: 'error', error: 'session_id and message are required' });
    }
    await callForSession(res, session_id, 'reply', { session_id, message });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`\nQuoteForge server running at http://localhost:${PORT}\n`);
});
