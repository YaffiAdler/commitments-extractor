"""Minimal API calls with the selected provider's own client and settings
(``CE_PROVIDER``: ``gemini`` or ``claude``), to tell "the connection does not
work" from "our request is not valid".

1. text only: the provider's structured-output call with a one-field schema, no image;
2. one small dummy image (digits drawn on a blank crop): ``read_number``.

Both go through the provider exactly as the pipeline does, with
``CE_DEBUG_PROVIDER=1``. Prints outcomes and response shapes only (status, API
error status, finish reason, part kinds, token counts), never the key or answer text.

Usage: CE_PROVIDER=gemini python scripts/check_provider.py
"""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("CE_DEBUG_PROVIDER", "1")

from app.config import Settings  # noqa: E402
from app.providers import claude, create_provider, gemini  # noqa: E402
from app.providers.common import NUMBER_SCHEMA  # noqa: E402
from app.providers.diagnostics import exception_chain  # noqa: E402

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DUMMY_DIGITS = "12345"
CROP_SIZE = (80, 240)  # height, width
TEXT_ONLY_INSTRUCTION = "Return the value null."


def _dummy_crop() -> np.ndarray:
    crop = np.full((*CROP_SIZE, 3), 255, np.uint8)
    cv2.putText(crop, DUMMY_DIGITS, (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    return crop


def _run(name: str, call: Callable[[], object]) -> bool:
    try:
        call()
    except Exception as exc:  # the point of the script: report the failure's shape
        print(f"{name}: FAILED {exception_chain(exc)}")
        return False
    print(f"{name}: OK")
    return True


def main() -> int:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format=LOG_FORMAT)
    settings = Settings()
    provider = create_provider(settings)
    if isinstance(provider, gemini.GeminiProvider):
        key_env, model, timeout_s = gemini.API_KEY_ENV, settings.gemini_model, settings.gemini_timeout_s
        text_only = lambda: provider._generate_json([], TEXT_ONLY_INSTRUCTION, NUMBER_SCHEMA)  # noqa: E731
    elif isinstance(provider, claude.ClaudeProvider):
        key_env, model, timeout_s = claude.API_KEY_ENV, settings.claude_model, settings.claude_timeout_s
        text_only = lambda: provider._call_tool([], TEXT_ONLY_INSTRUCTION, "record_number",  # noqa: E731
                                                "Record the value.", NUMBER_SCHEMA)
    else:
        print(f"CE_PROVIDER={settings.provider.value}: only gemini and claude make API calls")
        return 1
    key = os.environ.get(key_env, "")
    print(f"provider={settings.provider.value} model={model} http_timeout_s={timeout_s} "
          f"{key_env} {'set' if key else 'NOT SET'} (length {len(key)})")
    print("proxy variables set:", sorted(k for k in os.environ if "proxy" in k.lower()) or "none")
    text_ok = _run("1. text only", text_only)
    image_ok = _run("2. one small image", lambda: provider.read_number(_dummy_crop()))
    return 0 if text_ok and image_ok else 1


if __name__ == "__main__":
    sys.exit(main())
