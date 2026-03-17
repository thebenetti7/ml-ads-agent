"""
Logging Estruturado — JSON logging para producao + console para debug.

- Arquivo: logs/agent.log (JSON, rotacao 10MB, 10 arquivos)
- Console: formato legivel (quando headless=false ou debug)
"""

import json
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Formatter que gera JSON estruturado."""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }

        # Campos extras (account, action_id, duration_ms)
        for key in ("account", "action_id", "duration_ms", "rule_id"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(log_dir: str = "./logs", level: str = "INFO", console: bool = True):
    """
    Configura logging estruturado.

    Args:
        log_dir: diretorio para arquivos de log
        level: nivel de log (DEBUG, INFO, WARNING, ERROR)
        console: se True, tambem loga no console
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Limpar handlers existentes
    root.handlers.clear()

    # Handler de arquivo (JSON, rotacao)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_path / "agent.log"),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(JSONFormatter())
    file_handler.setLevel(logging.INFO)
    root.addHandler(file_handler)

    # Handler de console (formato legivel)
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        root.addHandler(console_handler)

    # Reduzir verbosidade de libs externas
    for lib in ("httpx", "httpcore", "uvicorn.access", "asyncio"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    logging.info("Logging configurado: arquivo=%s, console=%s", log_path / "agent.log", console)
