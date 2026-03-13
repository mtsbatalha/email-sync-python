import signal
from imap_client import ImapAccount
from progress import ProgressBar


class SyncEngine:
    def __init__(self, src_config, dst_config, batch_size=25,
                 log_callback=None, progress_callback=None):
        self.src_config = src_config
        self.dst_config = dst_config
        self.batch_size = batch_size
        self._log = log_callback or (lambda msg: None)
        self._progress = progress_callback or (lambda *a, **kw: None)
        self._aborted = False

        # Set up Ctrl+C handler
        self._prev_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _handle_sigint(self, signum, frame):
        self._aborted = True
        print('\n\n  Aborting... (finishing current batch)')

    def _restore_sigint(self):
        signal.signal(signal.SIGINT, self._prev_handler)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync(self, src_mailbox=None, dst_mailbox=None, all_mailboxes=False):
        """
        Sync emails.
        - all_mailboxes=True  → sync every mailbox from source to destination
        - otherwise           → sync src_mailbox → dst_mailbox (defaults to same name)
        Returns dict: {synced, skipped, errors}
        """
        total_synced = 0
        total_skipped = 0
        total_errors = 0

        try:
            if all_mailboxes:
                # Use a temporary connection just to list mailboxes
                tmp = ImapAccount(self.src_config)
                self._log('Connecting to source ({}) to list mailboxes...'.format(self.src_config.host))
                tmp.connect()
                mailboxes = tmp.list_mailboxes()
                tmp.disconnect()
                self._log('Found {} mailbox(es) to sync.'.format(len(mailboxes)))
                for mb in mailboxes:
                    if self._aborted:
                        break
                    s, sk, e = self._sync_mailbox(mb, mb)
                    total_synced += s
                    total_skipped += sk
                    total_errors += e
            else:
                dst_mb = dst_mailbox or src_mailbox
                s, sk, e = self._sync_mailbox(src_mailbox, dst_mb)
                total_synced += s
                total_skipped += sk
                total_errors += e

        except Exception as exc:
            self._log('Fatal error: {}'.format(exc))
            total_errors += 1
        finally:
            self._restore_sigint()

        return {'synced': total_synced, 'skipped': total_skipped, 'errors': total_errors}

    # ------------------------------------------------------------------
    # Per-mailbox sync
    # ------------------------------------------------------------------

    def _sync_mailbox(self, src_mb, dst_mb):
        synced = 0
        skipped = 0
        errors = 0

        self._log('--- Mailbox: {} -> {} ---'.format(src_mb, dst_mb))

        # Fresh connections per mailbox — avoids SSL drop on long-running sessions
        src = ImapAccount(self.src_config)
        dst = ImapAccount(self.dst_config)
        try:
            src.connect()
            dst.connect()
        except Exception as e:
            self._log('Connection error for mailbox "{}": {}'.format(src_mb, e))
            try: src.disconnect()
            except Exception: pass
            try: dst.disconnect()
            except Exception: pass
            return synced, skipped, errors

        try:
            # Ensure destination mailbox exists
            try:
                dst.ensure_mailbox_exists(dst_mb)
            except Exception as e:
                self._log('Could not create/open destination mailbox "{}": {}'.format(dst_mb, e))
                return synced, skipped, errors

            # Get all UIDs from source
            try:
                all_uids = src.get_uid_list(src_mb)
            except Exception as e:
                self._log('Could not list messages in source "{}": {}'.format(src_mb, e))
                return synced, skipped, errors

            total = len(all_uids)
            self._log('Found {} message(s) in source mailbox "{}".'.format(total, src_mb))

            if total == 0:
                self._log('Nothing to sync in "{}".'.format(src_mb))
                return synced, skipped, errors

            # Get existing Message-IDs from destination (dedup)
            self._log('Checking existing messages in destination "{}"...'.format(dst_mb))
            try:
                existing_ids = dst.get_existing_message_ids(dst_mb)
            except Exception as e:
                self._log('Could not read destination mailbox "{}": {}'.format(dst_mb, e))
                existing_ids = set()
            self._log('{} existing message(s) found in destination.'.format(len(existing_ids)))

            # Build batches
            batches = [all_uids[i:i + self.batch_size]
                       for i in range(0, total, self.batch_size)]
            total_batches = len(batches)
            processed = 0

            bar = ProgressBar(total, self._get_logger())

            for batch_num, batch_uids in enumerate(batches, 1):
                if self._aborted:
                    self._log('Sync aborted by user.')
                    break

                # Pass 1: fetch Message-IDs to identify new messages
                try:
                    mid_map = src.fetch_message_ids(batch_uids)
                except Exception as e:
                    self._log('Error fetching headers for batch {}: {} — reconnecting src...'.format(batch_num, e))
                    try:
                        src.disconnect()
                    except Exception:
                        pass
                    try:
                        src.connect()
                        src.get_uid_list(src_mb)  # re-SELECT the mailbox
                        mid_map = src.fetch_message_ids(batch_uids)
                    except Exception as e2:
                        self._log('Retry failed for batch {}: {}'.format(batch_num, e2))
                        processed += len(batch_uids)
                        errors += len(batch_uids)
                        bar.update(processed, synced, skipped, batch_num, total_batches)
                        continue

                new_uids = []
                for uid in batch_uids:
                    mid = mid_map.get(uid)
                    if mid and mid in existing_ids:
                        skipped += 1
                        processed += 1
                    else:
                        new_uids.append(uid)

                if not new_uids:
                    bar.update(processed, synced, skipped, batch_num, total_batches)
                    continue

                # Pass 2: fetch full source for new messages only
                try:
                    raw_map = src.fetch_raw_messages(new_uids)
                except Exception as e:
                    self._log('Error fetching messages for batch {}: {} — reconnecting src...'.format(batch_num, e))
                    try:
                        src.disconnect()
                    except Exception:
                        pass
                    try:
                        src.connect()
                        src.get_uid_list(src_mb)  # re-SELECT the mailbox
                        raw_map = src.fetch_raw_messages(new_uids)
                    except Exception as e2:
                        self._log('Retry failed for batch {}: {}'.format(batch_num, e2))
                        processed += len(new_uids)
                        errors += len(new_uids)
                        bar.update(processed, synced, skipped, batch_num, total_batches)
                        continue

                for uid in new_uids:
                    if uid not in raw_map:
                        errors += 1
                        processed += 1
                        continue
                    raw, internaldate = raw_map[uid]
                    try:
                        dst.append_message(dst_mb, raw, internaldate)
                    except Exception as e:
                        # SSL connection may have dropped — reconnect once and retry
                        self._log('Error appending UID {}: {} — reconnecting dst...'.format(uid, e))
                        try:
                            dst.disconnect()
                        except Exception:
                            pass
                        try:
                            dst.connect()
                            dst.ensure_mailbox_exists(dst_mb)
                            dst.append_message(dst_mb, raw, internaldate)
                        except Exception as e2:
                            self._log('Retry failed for UID {}: {}'.format(uid, e2))
                            errors += 1
                            processed += 1
                            continue
                    mid = mid_map.get(uid)
                    if mid:
                        existing_ids.add(mid)
                    synced += 1
                    processed += 1

                bar.update(processed, synced, skipped, batch_num, total_batches)

            bar.finish()
            self._log(
                'Mailbox "{}" done. Copied: {}  Skipped: {}  Errors: {}'.format(
                    src_mb, synced, skipped, errors
                )
            )
            return synced, skipped, errors

        finally:
            try: src.disconnect()
            except Exception: pass
            try: dst.disconnect()
            except Exception: pass

    def _get_logger(self):
        """Return a Logger-compatible object that uses our log callback."""
        return _CallbackLogger(self._log, self._progress)


class _CallbackLogger:
    """Minimal Logger interface that delegates to callbacks, used by ProgressBar."""
    def __init__(self, log_cb, progress_cb):
        self._log_cb = log_cb
        self._progress_cb = progress_cb
        self._last_was_progress = False

    def progress_line(self, text):
        import sys
        sys.stdout.write('\r' + text)
        sys.stdout.flush()
        self._last_was_progress = True

    def newline(self):
        if self._last_was_progress:
            import sys
            sys.stdout.write('\n')
            sys.stdout.flush()
            self._last_was_progress = False
