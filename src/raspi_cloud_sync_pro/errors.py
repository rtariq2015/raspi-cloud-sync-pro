"""Stable error codes suitable for persistent history and a future dashboard."""


class BackupError(Exception):
    """Do not put credentials, subprocess output, or arbitrary exception text here."""

    def __init__(self, code: str, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
