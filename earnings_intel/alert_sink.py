"""
Alert Engine — delivery sinks.

Writes every alert to a rolling daily log and an Excel-friendly CSV, and (if
configured) pushes it to Telegram. Also provides a persisted de-duplication
store so a restart of the scanner never re-fires alerts it already sent.

Telegram is configured via environment variables:
    TELEGRAM_BOT_TOKEN   (from @BotFather)
    TELEGRAM_CHAT_ID     (your chat / channel id)
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

log = logging.getLogger("technofunda.alert")

_CSV_FIELDS = ["timestamp", "source", "symbol", "kind", "action", "score",
               "headline", "url"]


class SeenStore:
    """Persisted set of announcement uids so we alert each filing only once."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._seen: set[str] = set()
        if self.path.exists():
            try:
                self._seen = set(json.loads(self.path.read_text()))
            except Exception:  # noqa: BLE001
                self._seen = set()

    def has(self, uid: str) -> bool:
        return uid in self._seen

    def add(self, uid: str) -> None:
        self._seen.add(uid)

    def save(self) -> None:
        try:
            # cap growth so the file does not balloon forever
            data = list(self._seen)[-20000:]
            self.path.write_text(json.dumps(data))
        except Exception as e:  # noqa: BLE001
            log.warning("could not persist seen store: %s", e)


@dataclass
class AlertSink:
    alerts_dir: Path
    telegram_token: Optional[str] = None
    telegram_chat: Optional[str] = None
    _csv_path: Path = field(init=False)

    def __post_init__(self):
        self.alerts_dir = Path(self.alerts_dir)
        self.alerts_dir.mkdir(parents=True, exist_ok=True)
        self.telegram_token = self.telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_chat = self.telegram_chat or os.environ.get("TELEGRAM_CHAT_ID")
        self._csv_path = self.alerts_dir / "alerts.csv"
        if not self._csv_path.exists():
            with self._csv_path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(_CSV_FIELDS)

    # --------------------------------------------------------------- logging
    def _log_path(self) -> Path:
        return self.alerts_dir / f"scanner_{datetime.now():%Y%m%d}.log"

    def info(self, msg: str) -> None:
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
        print(line, flush=True)
        try:
            with self._log_path().open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- alerts
    def emit(self, body: str, meta: dict) -> None:
        """Record one alert across all configured channels."""
        self.info("ALERT\n" + body + "\n")
        try:
            with self._csv_path.open("a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([
                    datetime.now().isoformat(timespec="seconds"),
                    meta.get("source", ""), meta.get("symbol", ""),
                    meta.get("kind", ""), meta.get("action", ""),
                    meta.get("score", ""), meta.get("headline", "")[:200],
                    meta.get("url", ""),
                ])
        except Exception as e:  # noqa: BLE001
            log.warning("CSV write failed: %s", e)
        self._telegram(body)

    def _telegram(self, text: str) -> None:
        if not (self.telegram_token and self.telegram_chat):
            return
        if requests is None:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                data={"chat_id": self.telegram_chat, "text": text},
                timeout=10)
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram send failed: %s", e)
