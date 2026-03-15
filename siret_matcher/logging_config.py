"""Logging structuré JSON pour SIRET Matcher."""
import json
import logging
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Formateur qui produit une ligne JSON par log."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
                         .strftime("%Y-%m-%dT%H:%M:%S.") +
                         f"{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Champs structurés attachés via extra=
        if hasattr(record, "extra_fields"):
            entry.update(record.extra_fields)
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging() -> None:
    """Configure le logging racine siret_matcher en JSON sur stdout."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger("siret_matcher")
    # Éviter les doublons si appelé plusieurs fois
    if root_logger.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.setLevel(level)
    root_logger.addHandler(handler)

    # Logger pour api.py (module racine, pas dans le package)
    api_logger = logging.getLogger("api")
    if not api_logger.handlers:
        api_handler = logging.StreamHandler(sys.stdout)
        api_handler.setFormatter(JSONFormatter())
        api_logger.setLevel(level)
        api_logger.addHandler(api_handler)

    # Supprimer les handlers du root logger pour éviter les doublons
    # (basicConfig aurait pu être appelé ailleurs)
    logging.root.handlers.clear()


def log_structured(logger: logging.Logger, level: int, message: str, **fields) -> None:
    """Émet un log structuré avec des champs supplémentaires."""
    record = logger.makeRecord(
        name=logger.name,
        level=level,
        fn="",
        lno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.extra_fields = fields
    logger.handle(record)
