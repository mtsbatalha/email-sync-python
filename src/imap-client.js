'use strict';

const { ImapFlow } = require('imapflow');

function buildImapConfig({ host, port, encryption, user, password }) {
  const secure = encryption === 'SSL/TLS';
  return {
    host,
    port: parseInt(port, 10),
    secure,
    auth: { user, pass: password },
    tls: { rejectUnauthorized: false },
    logger: false,
    socketTimeout: 120000,   // 2 min — throw if no data received
    connectionTimeout: 30000, // 30 sec to establish connection
  };
}

async function testConnection(config) {
  const client = new ImapFlow(buildImapConfig(config));
  client.on('error', () => {});
  try {
    await client.connect();
    await client.logout();
    return { success: true, error: null };
  } catch (err) {
    try { client.close(); } catch (_) {}
    return { success: false, error: err.message };
  }
}

async function listMailboxes(config) {
  const client = new ImapFlow(buildImapConfig(config));
  client.on('error', () => {});
  try {
    await client.connect();
    const list = await client.list();
    await client.logout();

    const mailboxes = list
      .filter(mb => !mb.flags.has('\\Noselect'))
      .map(mb => mb.path)
      .sort((a, b) => {
        if (a === 'INBOX') return -1;
        if (b === 'INBOX') return 1;
        return a.localeCompare(b);
      });

    return { mailboxes, error: null };
  } catch (err) {
    try { client.close(); } catch (_) {}
    return { mailboxes: [], error: err.message };
  }
}

module.exports = { buildImapConfig, testConnection, listMailboxes };
