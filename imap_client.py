import imaplib
import ssl
import socket
import re

# Default socket timeout in seconds (matches Node.js socketTimeout: 120000)
SOCKET_TIMEOUT = 120


class AccountConfig:
    def __init__(self, host, port, encryption, user, password):
        self.host = host
        self.port = int(port)
        self.encryption = encryption  # 'SSL/TLS' | 'STARTTLS' | 'None'
        self.user = user
        self.password = password

    def __repr__(self):
        return '{}@{}:{} ({})'.format(self.user, self.host, self.port, self.encryption)


def _make_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class ImapAccount:
    def __init__(self, config):
        self.config = config
        self.conn = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self):
        socket.setdefaulttimeout(SOCKET_TIMEOUT)
        enc = self.config.encryption
        if enc == 'SSL/TLS':
            self._connect_ssl()
        elif enc == 'STARTTLS':
            self._connect_starttls()
        else:
            self._connect_plain()
        self.conn.login(self.config.user, self.config.password)

    def _connect_ssl(self):
        ctx = _make_ssl_context()
        self.conn = imaplib.IMAP4_SSL(self.config.host, self.config.port, ssl_context=ctx)

    def _connect_starttls(self):
        self.conn = imaplib.IMAP4(self.config.host, self.config.port)
        ctx = _make_ssl_context()
        self.conn.starttls(ssl_context=ctx)

    def _connect_plain(self):
        self.conn = imaplib.IMAP4(self.config.host, self.config.port)

    def disconnect(self):
        if self.conn is None:
            return
        try:
            self.conn.logout()
        except Exception:
            try:
                self.conn.shutdown()
            except Exception:
                pass
        finally:
            self.conn = None

    def test_connection(self):
        """Returns (success: bool, error_message: str | None)."""
        try:
            self.connect()
            self.disconnect()
            return True, None
        except Exception as e:
            self.conn = None
            return False, str(e)

    # ------------------------------------------------------------------
    # Mailbox listing
    # ------------------------------------------------------------------

    def list_mailboxes(self):
        """Returns sorted list of selectable mailbox path strings (INBOX first)."""
        typ, data = self.conn.list()
        if typ != 'OK':
            return []
        mailboxes = []
        for item in data:
            if item is None:
                continue
            line = item.decode('utf-8', errors='replace') if isinstance(item, bytes) else item
            # Skip non-selectable mailboxes
            if r'\Noselect' in line:
                continue
            # Parse: (\Flags) "delimiter" "Name" or (\Flags) "delimiter" Name
            m = re.search(r'\([^)]*\)\s+"[^"]*"\s+"?([^"]+)"?\s*$', line)
            if m:
                name = m.group(1).strip()
                if name:
                    mailboxes.append(name)
        # INBOX first, then alphabetical
        mailboxes = list(dict.fromkeys(mailboxes))  # dedup preserving order
        mailboxes.sort(key=lambda x: (0 if x.upper() == 'INBOX' else 1, x.upper()))
        return mailboxes

    def count_messages(self, mailbox):
        """Returns the number of messages in a mailbox."""
        typ, data = self.conn.select(_quote_mailbox(mailbox), readonly=True)
        if typ != 'OK':
            return 0
        try:
            return int(data[0])
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def get_existing_message_ids(self, mailbox, batch_size=500):
        """Returns set of Message-ID strings already in destination mailbox."""
        typ, data = self.conn.select(_quote_mailbox(mailbox), readonly=True)
        if typ != 'OK':
            return set()
        typ, data = self.conn.uid('SEARCH', 'ALL')
        if typ != 'OK' or not data or not data[0]:
            return set()
        uids = data[0].split()
        if not uids:
            return set()
        existing = set()
        # Fetch in batches to avoid "Too long argument" on large mailboxes
        for i in range(0, len(uids), batch_size):
            batch = uids[i:i + batch_size]
            uid_set = b','.join(batch)
            typ, data = self.conn.uid('FETCH', uid_set, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])')
            if typ != 'OK':
                continue
            for item in data:
                if isinstance(item, tuple):
                    header = item[1].decode('utf-8', errors='replace') if isinstance(item[1], bytes) else item[1]
                    mid = _extract_message_id(header)
                    if mid:
                        existing.add(mid)
        return existing

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def get_uid_list(self, mailbox):
        """Returns list of UID byte strings from source mailbox."""
        typ, data = self.conn.select(_quote_mailbox(mailbox), readonly=True)
        if typ != 'OK':
            return []
        typ, data = self.conn.uid('SEARCH', 'ALL')
        if typ != 'OK' or not data or not data[0]:
            return []
        uids = data[0].split()
        return uids  # list of bytes like [b'1', b'2', ...]

    def fetch_message_ids(self, uids):
        """
        Fetch Message-ID headers for a list of UIDs.
        Returns {uid_bytes: message_id_str_or_None}.
        """
        if not uids:
            return {}
        uid_set = b','.join(uids)
        typ, data = self.conn.uid(
            'FETCH', uid_set, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])'
        )
        if typ != 'OK':
            return {}
        result = {}
        i = 0
        while i < len(data):
            item = data[i]
            if isinstance(item, tuple):
                # item[0] is like b'123 (UID 456 BODY[...]  {size})'
                uid = _parse_uid_from_fetch_response(item[0])
                header = item[1].decode('utf-8', errors='replace') if isinstance(item[1], bytes) else (item[1] or '')
                mid = _extract_message_id(header)
                if uid:
                    result[uid] = mid
            i += 1
        return result

    def fetch_raw_messages(self, uids):
        """
        Fetch full RFC822 source + INTERNALDATE for a list of UIDs.
        Returns {uid_bytes: (raw_bytes, internaldate_str)}.
        """
        if not uids:
            return {}
        uid_set = b','.join(uids)
        typ, data = self.conn.uid('FETCH', uid_set, '(RFC822 INTERNALDATE)')
        if typ != 'OK':
            return {}
        result = {}
        i = 0
        while i < len(data):
            item = data[i]
            if isinstance(item, tuple):
                header_line = item[0].decode('utf-8', errors='replace') if isinstance(item[0], bytes) else (item[0] or '')
                uid = _parse_uid_from_fetch_response(item[0])
                raw = item[1] if isinstance(item[1], bytes) else b''
                internaldate = _parse_internaldate(header_line)
                if uid:
                    result[uid] = (raw, internaldate)
            i += 1
        return result

    # ------------------------------------------------------------------
    # Appending
    # ------------------------------------------------------------------

    def append_message(self, mailbox, raw, internaldate):
        """Append a raw message to destination mailbox preserving date."""
        date_time = None
        if internaldate:
            try:
                date_time = imaplib.Internaldate2tuple(
                    ('OK', [internaldate.encode() if isinstance(internaldate, str) else internaldate])
                )
            except Exception:
                date_time = None
        self.conn.append(_quote_mailbox(mailbox), '', date_time, raw)

    def ensure_mailbox_exists(self, mailbox):
        """Select mailbox; create it if it doesn't exist."""
        typ, _ = self.conn.select(_quote_mailbox(mailbox))
        if typ != 'OK':
            self.conn.create(_quote_mailbox(mailbox))
            self.conn.select(_quote_mailbox(mailbox))


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _quote_mailbox(name):
    """Quote mailbox name for IMAP if it contains special characters."""
    if ' ' in name or name.upper() != name.upper().encode('ascii', errors='replace').decode('ascii', errors='replace'):
        return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return name


def _extract_message_id(header_text):
    """Extract Message-ID value from a header string."""
    m = re.search(r'[Mm]essage-[Ii][Dd]\s*:\s*<?([^>\r\n]+)>?', header_text)
    if m:
        return m.group(1).strip()
    return None


def _parse_uid_from_fetch_response(header_bytes):
    """Extract UID from IMAP FETCH response header bytes like b'123 (UID 456 ...)'."""
    if isinstance(header_bytes, bytes):
        text = header_bytes.decode('utf-8', errors='replace')
    else:
        text = header_bytes or ''
    m = re.search(r'UID\s+(\d+)', text)
    if m:
        return m.group(1).encode()
    # Fall back: first number at start of line
    m = re.match(r'(\d+)', text.strip())
    if m:
        return m.group(1).encode()
    return None


def _parse_internaldate(fetch_header):
    """Extract INTERNALDATE string from a FETCH response header line."""
    m = re.search(r'INTERNALDATE\s+"([^"]+)"', fetch_header)
    if m:
        return m.group(1)
    return None
