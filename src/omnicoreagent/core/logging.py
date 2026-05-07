import logging


logger = logging.getLogger("omnicoreagent")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
