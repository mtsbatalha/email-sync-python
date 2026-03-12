import sys
import datetime


class Logger:
    def __init__(self):
        self._last_was_progress = False

    def _timestamp(self):
        return datetime.datetime.now().strftime('%H:%M:%S')

    def _clear_progress_line(self):
        if self._last_was_progress:
            sys.stdout.write('\r' + ' ' * 80 + '\r')
            sys.stdout.flush()
            self._last_was_progress = False

    def log(self, message):
        self._clear_progress_line()
        print('[{}] [LOG] {}'.format(self._timestamp(), message))

    def error(self, message):
        self._clear_progress_line()
        print('[{}] [ERR] {}'.format(self._timestamp(), message))

    def info(self, message):
        self._clear_progress_line()
        print('[{}] [INF] {}'.format(self._timestamp(), message))

    def progress_line(self, text):
        """Overwrite current line without newline (for progress bar)."""
        sys.stdout.write('\r' + text)
        sys.stdout.flush()
        self._last_was_progress = True

    def newline(self):
        if self._last_was_progress:
            sys.stdout.write('\n')
            sys.stdout.flush()
            self._last_was_progress = False


class ProgressBar:
    def __init__(self, total, logger, width=40):
        self.total = max(total, 1)
        self.logger = logger
        self.width = width

    def update(self, current, synced, skipped, batch, total_batches):
        pct = int(current * 100 / self.total)
        filled = int(self.width * current / self.total)
        bar = '=' * filled
        if filled < self.width:
            bar += '>'
        bar = bar.ljust(self.width)
        line = '[{}] {:3d}%  {}/{}  Copied:{}  Skipped:{}  Batch:{}/{}'.format(
            bar, pct, current, self.total, synced, skipped, batch, total_batches
        )
        self.logger.progress_line(line)

    def finish(self):
        self.logger.newline()
