#!/usr/bin/env python3
"""
Email Sync CLI — Sync emails between two IMAP accounts interactively.
Requires Python 3.6+. Zero external dependencies.
"""
import sys
import getpass

from imap_client import AccountConfig, ImapAccount
from sync_engine import SyncEngine
from progress import Logger

VERSION = '1.0.0'

BANNER = """
============================================================
  EMAIL SYNC  v{}  —  Python CLI
  Sync emails between two IMAP accounts
  Zero dependencies  |  Python 3.6+
============================================================
""".format(VERSION)

DEFAULT_PORTS = {
    'SSL/TLS': 993,
    'STARTTLS': 143,
    'None': 143,
}

ENCRYPTION_OPTIONS = ['SSL/TLS', 'STARTTLS', 'None']
BATCH_OPTIONS = [10, 25, 50, 100]

logger = Logger()


# ------------------------------------------------------------------
# Input helpers
# ------------------------------------------------------------------

def prompt(label, default=None, required=True):
    """Prompt for text input. Returns default on empty input if provided."""
    if default is not None:
        display = '{} [{}]: '.format(label, default)
    else:
        display = '{}: '.format(label)
    while True:
        try:
            value = input('  ' + display).strip()
        except EOFError:
            sys.exit(0)
        if value:
            return value
        if default is not None:
            return str(default)
        if not required:
            return ''
        print('  (required, please enter a value)')


def prompt_password(label):
    """Prompt for password with hidden input."""
    while True:
        try:
            value = getpass.getpass('  {}: '.format(label))
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)
        if value:
            return value
        print('  (required, please enter a password)')


def prompt_int_choice(label, options, default=None):
    """
    Display numbered options and return the selected option value.
    options: list of values; display is 1-indexed.
    """
    for i, opt in enumerate(options, 1):
        marker = ' (default)' if opt == default else ''
        print('  [{}] {}{}'.format(i, opt, marker))
    while True:
        raw = prompt(label, default=1 if default is None else options.index(default) + 1)
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except (ValueError, TypeError):
            pass
        print('  Invalid choice. Enter a number between 1 and {}.'.format(len(options)))


# ------------------------------------------------------------------
# Account configuration
# ------------------------------------------------------------------

def configure_account(label):
    """Interactively configure an IMAP account. Returns AccountConfig."""
    print('\n--- {} ---'.format(label))
    print('  Encryption:')
    encryption = prompt_int_choice('Select encryption', ENCRYPTION_OPTIONS, default='SSL/TLS')
    default_port = DEFAULT_PORTS[encryption]
    host = prompt('Server')
    port = prompt('Port', default=default_port)
    user = prompt('Username')
    password = prompt_password('Password')
    try:
        port = int(port)
    except ValueError:
        port = default_port
    return AccountConfig(host, port, encryption, user, password)


# ------------------------------------------------------------------
# Menu actions
# ------------------------------------------------------------------

def action_test_connections(src, dst):
    print()
    print('  Testing source ({})...'.format(src.host), end=' ', flush=True)
    ok, err = ImapAccount(src).test_connection()
    print('OK' if ok else 'FAILED — {}'.format(err))

    print('  Testing destination ({})...'.format(dst.host), end=' ', flush=True)
    ok, err = ImapAccount(dst).test_connection()
    print('OK' if ok else 'FAILED — {}'.format(err))


def action_list_mailboxes(src, dst):
    print()
    for label, config in [('Source ({})'.format(src.host), src),
                           ('Destination ({})'.format(dst.host), dst)]:
        print('  {}:'.format(label))
        acc = ImapAccount(config)
        try:
            acc.connect()
            mailboxes = acc.list_mailboxes()
            acc.disconnect()
            if mailboxes:
                for mb in mailboxes:
                    print('    - {}'.format(mb))
            else:
                print('    (no mailboxes found)')
        except Exception as e:
            print('    ERROR: {}'.format(e))
        print()


def _pick_mailbox(account_config, prompt_label):
    """Connect, list mailboxes, let user pick one by number. Returns mailbox name."""
    acc = ImapAccount(account_config)
    try:
        acc.connect()
        mailboxes = acc.list_mailboxes()
        acc.disconnect()
    except Exception as e:
        print('  Could not list mailboxes: {}'.format(e))
        return None
    if not mailboxes:
        print('  No mailboxes found.')
        return None
    print()
    print('  {} mailboxes:'.format(prompt_label))
    for i, mb in enumerate(mailboxes, 1):
        print('    [{}] {}'.format(i, mb))
    raw = prompt('Select', default=1)
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(mailboxes):
            return mailboxes[idx]
    except (ValueError, TypeError):
        pass
    print('  Invalid selection.')
    return None


def action_sync(src, dst):
    print()
    print('  Sync mode:')
    mode = prompt_int_choice('Select', ['Sync a single mailbox', 'Sync ALL mailboxes'])

    src_mailbox = None
    dst_mailbox = None
    all_mailboxes = False

    if mode == 'Sync ALL mailboxes':
        all_mailboxes = True
    else:
        src_mailbox = _pick_mailbox(src, 'Source')
        if src_mailbox is None:
            return
        dst_mailbox_input = prompt(
            'Destination mailbox (blank = same as source: {})'.format(src_mailbox),
            default='',
            required=False
        )
        dst_mailbox = dst_mailbox_input if dst_mailbox_input else src_mailbox

    print()
    print('  Batch size:')
    batch_size = prompt_int_choice('Select', BATCH_OPTIONS, default=25)

    print()

    def log_cb(msg):
        logger.log(msg)

    def progress_cb(*args, **kwargs):
        pass  # ProgressBar handles output directly

    engine = SyncEngine(
        src, dst,
        batch_size=batch_size,
        log_callback=log_cb,
        progress_callback=progress_cb,
    )

    result = engine.sync(
        src_mailbox=src_mailbox,
        dst_mailbox=dst_mailbox,
        all_mailboxes=all_mailboxes,
    )

    print()
    print('  ┌─────────────────────┐')
    print('  │  Sync complete      │')
    print('  ├─────────────────────┤')
    print('  │  Copied : {:>8}  │'.format(result['synced']))
    print('  │  Skipped: {:>8}  │'.format(result['skipped']))
    print('  │  Errors : {:>8}  │'.format(result['errors']))
    print('  └─────────────────────┘')


# ------------------------------------------------------------------
# Main loop
# ------------------------------------------------------------------

MENU_OPTIONS = [
    'Test connections',
    'List mailboxes',
    'Sync emails',
    'Reconfigure accounts',
    'Exit',
]


def main_menu():
    print()
    print('  MAIN MENU')
    for i, opt in enumerate(MENU_OPTIONS, 1):
        print('  [{}] {}'.format(i, opt))
    raw = prompt('Choice')
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(MENU_OPTIONS):
            return MENU_OPTIONS[idx]
    except (ValueError, TypeError):
        pass
    return None


def main():
    print(BANNER)

    src = configure_account('SOURCE ACCOUNT [FROM]')
    dst = configure_account('DESTINATION ACCOUNT [TO]')

    while True:
        choice = main_menu()
        if choice is None:
            print('  Invalid choice.')
            continue

        if choice == 'Test connections':
            action_test_connections(src, dst)

        elif choice == 'List mailboxes':
            action_list_mailboxes(src, dst)

        elif choice == 'Sync emails':
            try:
                action_sync(src, dst)
            except KeyboardInterrupt:
                print('\n  Sync interrupted.')

        elif choice == 'Reconfigure accounts':
            print()
            print('  Which account would you like to reconfigure?')
            which = prompt_int_choice('Select', ['Source', 'Destination', 'Both'])
            if which in ('Source', 'Both'):
                src = configure_account('SOURCE ACCOUNT [FROM]')
            if which in ('Destination', 'Both'):
                dst = configure_account('DESTINATION ACCOUNT [TO]')

        elif choice == 'Exit':
            print('\n  Goodbye.')
            sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n\n  Interrupted. Goodbye.')
        sys.exit(0)
