import logging
import sys

# Configure default logger for aspose-pdf
_logger = logging.getLogger("aspose_pdf")

# Default configuration
if not _logger.handlers:
    _logger.setLevel(logging.INFO)
    _handler = logging.StreamHandler(sys.stdout)
    _formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    _handler.setFormatter(_formatter)
    _logger.addHandler(_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger for the given module name."""
    return _logger.getChild(name)
