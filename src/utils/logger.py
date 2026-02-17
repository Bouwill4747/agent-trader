import os
import re
import logging
from logging.handlers import RotatingFileHandler

from config.settings import LOG_PATH


class SecretRedactionFilter(logging.Filter):
    """Redacts known secret patterns from log messages.

    Prevents accidental credential leakage in error tracebacks
    and exception messages. Defense-in-depth measure (H-4).
    """

    PATTERNS = [
        # Ethereum/Polygon private keys (64 hex chars after 0x)
        (re.compile(r'0x[a-fA-F0-9]{64}'), '0x[REDACTED_KEY]'),
        # Anthropic API keys
        (re.compile(r'sk-ant-[a-zA-Z0-9_-]+'), '[REDACTED_ANTHROPIC_KEY]'),
        # Generic key=value patterns for common secret names
        (re.compile(
            r'(?i)(api[_-]?key|api[_-]?secret|passphrase|private[_-]?key|'
            r'client[_-]?secret|password|token)\s*[=:]\s*\S+'
        ), r'\1=[REDACTED]'),
    ]

    def filter(self, record):
        if isinstance(record.msg, str):
            for pattern, replacement in self.PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        # Also handle args-based formatting
        if record.args:
            new_args = []
            for arg in (record.args if isinstance(record.args, tuple) else (record.args,)):
                if isinstance(arg, str):
                    for pattern, replacement in self.PATTERNS:
                        arg = pattern.sub(replacement, arg)
                new_args.append(arg)
            record.args = tuple(new_args)
        return True


def setup_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Create a logger that writes to both console and rotating file.

    Includes a secret redaction filter to prevent credential leakage
    in log output.
    """

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Don't add handlers twice if logger already exists
    if logger.handlers:
        return logger

    # Add secret redaction filter
    logger.addFilter(SecretRedactionFilter())

    # Format: timestamp | module name | level | message
    formatter = logging.Formatter(
        "%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File handler — rotating at 10MB, keep 5 backups
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler — same format, prints to terminal
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
