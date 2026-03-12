'use strict';

const { ImapFlow } = require('imapflow');
const { buildImapConfig } = require('./imap-client');

function makeClient(config) {
  const client = new ImapFlow(buildImapConfig(config));
  client.on('error', err => {
    console.error('[imap error]', err.message);
    try { client.close(); } catch (_) {}
  });
  return client;
}

async function ensureMailboxExists(client, path) {
  try {
    await client.mailboxOpen(path);
  } catch (_) {
    await client.mailboxCreate(path);
    await client.mailboxOpen(path);
  }
}

// Returns a Set of Message-IDs already present in the destination mailbox
async function getExistingMessageIds(dstConfig, mailboxPath, sendEvent) {
  const ids = new Set();
  const client = makeClient(dstConfig);
  try {
    await client.connect();
    const info = await client.mailboxOpen(mailboxPath, { readOnly: true });
    if (info.exists > 0) {
      sendEvent('status', { message: `Checking existing emails in ${mailboxPath}...` });
      for await (const msg of client.fetch('1:*', { envelope: true })) {
        if (msg.envelope && msg.envelope.messageId) {
          ids.add(msg.envelope.messageId.trim());
        }
      }
    }
    await client.logout();
  } catch (err) {
    console.error('[getExistingMessageIds error]', err.message);
    try { client.close(); } catch (_) {}
  }
  return ids;
}

async function syncEmails(params, sendEvent, isAborted) {
  const {
    srcHost, srcPort, srcEncryption, srcUser, srcPass, srcMailbox,
    dstHost, dstPort, dstEncryption, dstUser, dstPass, dstMailbox,
    allMailboxes, batchSize,
  } = params;

  const batchSizeNum = parseInt(batchSize, 10) || 25;

  const srcConfig = { host: srcHost, port: srcPort, encryption: srcEncryption, user: srcUser, password: srcPass };
  const dstConfig = { host: dstHost, port: dstPort, encryption: dstEncryption, user: dstUser, password: dstPass };

  // ── Phase 1: list mailboxes & count emails ───────────────────────────────
  const srcClient = makeClient(srcConfig);
  try {
    sendEvent('status', { message: 'Connecting to source account...' });
    await srcClient.connect();
    sendEvent('log', { message: `Connected to source: ${srcHost}` });

    // Determine mailboxes to sync
    let mailboxesToSync = [];
    if (allMailboxes === 'true' || allMailboxes === true) {
      const list = await srcClient.list();
      mailboxesToSync = list
        .filter(mb => !mb.flags.has('\\Noselect'))
        .map(mb => mb.path);
      sendEvent('log', { message: `Found ${mailboxesToSync.length} mailboxes to sync` });
    } else {
      mailboxesToSync = [srcMailbox || 'INBOX'];
    }

    // Count total emails
    sendEvent('status', { message: 'Counting emails...' });
    let totalEmails = 0;
    const mailboxCounts = {};
    for (const mb of mailboxesToSync) {
      try {
        const info = await srcClient.mailboxOpen(mb, { readOnly: true });
        mailboxCounts[mb] = info.exists;
        totalEmails += info.exists;
        await srcClient.mailboxClose();
      } catch (err) {
        mailboxCounts[mb] = 0;
        try { await srcClient.mailboxClose(); } catch (_) {}
        sendEvent('error', { message: `Could not open mailbox "${mb}": ${err.message}` });
      }
    }
    sendEvent('log', { message: `Total emails to sync: ${totalEmails}` });
    sendEvent('progress', { current: 0, total: totalEmails, batch: 0, totalBatches: 0, skipped: 0 });

    let syncedCount = 0;
    let skippedCount = 0;
    let errorCount = 0;
    let globalBatch = 0;

    // ── Phase 2: sync each mailbox ─────────────────────────────────────────
    for (const srcMb of mailboxesToSync) {
      if (isAborted()) break;

      const count = mailboxCounts[srcMb];
      if (count === 0) {
        sendEvent('log', { message: `Skipping empty mailbox: ${srcMb}` });
        continue;
      }

      sendEvent('status', { message: `Syncing mailbox: ${srcMb}` });

      const targetDstMb = (allMailboxes === 'true' || allMailboxes === true)
        ? srcMb
        : (dstMailbox || 'INBOX');

      // ── Fresh dstClient per mailbox ──────────────────────────────────────
      const dstClient = makeClient(dstConfig);
      let dstConnected = false;
      try {
        await dstClient.connect();
        dstConnected = true;
        sendEvent('log', { message: `Connected to destination for "${targetDstMb}"` });
      } catch (err) {
        sendEvent('error', { message: `Cannot connect to destination for "${targetDstMb}": ${err.message}` });
        continue;
      }

      try {
        // Ensure destination mailbox exists
        try {
          await ensureMailboxExists(dstClient, targetDstMb);
          await dstClient.mailboxClose();
        } catch (err) {
          try { await dstClient.mailboxClose(); } catch (_) {}
          sendEvent('error', { message: `Failed to open/create "${targetDstMb}": ${err.message}` });
          continue;
        }

        // Get existing Message-IDs using a separate short-lived connection
        sendEvent('log', { message: `Checking duplicates in "${targetDstMb}"...` });
        const existingIds = await getExistingMessageIds(dstConfig, targetDstMb, sendEvent);
        sendEvent('log', { message: `${existingIds.size} existing email(s) found in destination "${targetDstMb}"` });

        // Open source mailbox
        try {
          await srcClient.mailboxOpen(srcMb, { readOnly: true });
        } catch (err) {
          sendEvent('error', { message: `Failed to open source mailbox "${srcMb}": ${err.message}` });
          continue;
        }

        // Get all UIDs
        let uids = [];
        try {
          uids = await srcClient.search({ all: true }, { uid: true });
        } catch (err) {
          sendEvent('error', { message: `Failed to search "${srcMb}": ${err.message}` });
          try { await srcClient.mailboxClose(); } catch (_) {}
          continue;
        }

        if (uids.length === 0) {
          sendEvent('log', { message: `No messages in ${srcMb}` });
          await srcClient.mailboxClose();
          continue;
        }

        const totalBatches = Math.ceil(uids.length / batchSizeNum);
        sendEvent('log', { message: `${uids.length} messages in ${srcMb}, ${totalBatches} batch(es)` });

        for (let i = 0; i < uids.length; i += batchSizeNum) {
          if (isAborted()) break;

          const batchUids = uids.slice(i, i + batchSizeNum);
          const batchNum = Math.floor(i / batchSizeNum) + 1;
          globalBatch++;

          sendEvent('status', { message: `${srcMb}: batch ${batchNum}/${totalBatches}` });

          try {
            // Pass 1: fetch only envelopes to find which UIDs actually need copying
            const uidsToFetch = [];
            const uidMsgIdMap = new Map();
            for await (const msg of srcClient.fetch(batchUids, { envelope: true }, { uid: true })) {
              if (isAborted()) break;
              const msgId = msg.envelope?.messageId?.trim() ?? null;
              if (msgId && existingIds.has(msgId)) {
                skippedCount++;
                sendEvent('progress', { current: syncedCount + skippedCount, total: totalEmails, batch: globalBatch, totalBatches, skipped: skippedCount });
              } else {
                uidsToFetch.push(msg.uid);
                uidMsgIdMap.set(msg.uid, msgId);
              }
            }

            if (isAborted()) break;

            // Pass 2: fetch full source only for new emails
            if (uidsToFetch.length > 0) {
              for await (const msg of srcClient.fetch(uidsToFetch, { source: true, internalDate: true }, { uid: true })) {
                if (isAborted()) break;
                const msgId = uidMsgIdMap.get(msg.uid) ?? null;
                try {
                  await dstClient.append(targetDstMb, msg.source, [], msg.internalDate);
                  syncedCount++;
                  if (msgId) existingIds.add(msgId);
                  sendEvent('progress', { current: syncedCount + skippedCount, total: totalEmails, batch: globalBatch, totalBatches, skipped: skippedCount });
                  sendEvent('log', { message: `Copied UID ${msg.uid} → ${targetDstMb}` });
                } catch (err) {
                  errorCount++;
                  sendEvent('error', { message: `Failed UID ${msg.uid}: ${err.message}` });
                }
              }
            }
          } catch (err) {
            sendEvent('error', { message: `Batch ${batchNum} fetch error in ${srcMb}: ${err.message}` });
          }
        }

        try { await srcClient.mailboxClose(); } catch (_) {}

      } finally {
        if (dstConnected) {
          try { await dstClient.logout(); } catch (_) { try { dstClient.close(); } catch (__) {} }
        }
      }
    }

    sendEvent('complete', { totalSynced: syncedCount, skipped: skippedCount, errors: errorCount });

  } finally {
    try { await srcClient.logout(); } catch (_) { try { srcClient.close(); } catch (__) {} }
  }
}

module.exports = { syncEmails };
