'use strict';

const express = require('express');
const cors = require('cors');
const path = require('path');
const { testConnection, listMailboxes } = require('./src/imap-client');
const { syncEmails } = require('./src/sync-engine');

// Prevent IMAP socket timeouts / network errors from crashing the server
process.on('uncaughtException', err => {
  console.error('[uncaughtException]', err.message);
});
process.on('unhandledRejection', (reason) => {
  console.error('[unhandledRejection]', reason);
});

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// POST /api/test-connections
app.post('/api/test-connections', async (req, res) => {
  const { source, destination } = req.body;
  try {
    const [srcResult, dstResult] = await Promise.all([
      testConnection(source),
      testConnection(destination),
    ]);
    res.json({ source: srcResult, destination: dstResult });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// POST /api/list-mailboxes
app.post('/api/list-mailboxes', async (req, res) => {
  try {
    const result = await listMailboxes(req.body);
    res.json(result);
  } catch (err) {
    res.status(500).json({ mailboxes: [], error: err.message });
  }
});

// POST /api/delete-mailbox
app.post('/api/delete-mailbox', async (req, res) => {
  const { ImapFlow } = require('imapflow');
  const { buildImapConfig } = require('./src/imap-client');
  const { host, port, encryption, user, password, mailbox } = req.body;

  const client = new ImapFlow(buildImapConfig({ host, port, encryption, user, password }));
  try {
    await client.connect();
    await client.mailboxOpen(mailbox);
    const uids = await client.search({ all: true }, { uid: true });
    let deleted = 0;
    if (uids.length > 0) {
      await client.messageDelete(uids, { uid: true });
      deleted = uids.length;
    }
    await client.logout();
    res.json({ deleted });
  } catch (err) {
    try { client.close(); } catch (_) {}
    res.status(500).json({ deleted: 0, error: err.message });
  }
});

// GET /api/sync  (SSE)
app.get('/api/sync', async (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders();

  let aborted = false;
  req.on('close', () => { aborted = true; });

  const sendEvent = (type, data) => {
    if (!res.writableEnded) {
      res.write(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`);
    }
  };

  try {
    await syncEmails(req.query, sendEvent, () => aborted);
  } catch (err) {
    sendEvent('fatal', { message: err.message });
  } finally {
    if (!res.writableEnded) res.end();
  }
});

app.listen(PORT, () => {
  console.log(`Email Sync server running at http://localhost:${PORT}`);
});
