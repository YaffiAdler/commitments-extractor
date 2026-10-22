"""Check the Gemini API connection with the provider's exact request shape.

Sends a blank dummy page (no patient data) and prints the HTTP status and the
API's answer, so connection, key and model problems can be diagnosed.
Usage: python scripts/check_gemini.py [model]
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import Settings  # noqa: E402
from app.pipeline.ingest import PageImage  # noqa: E402
from app.providers.common import FORM_READING_SCHEMA, SYSTEM_PROMPT  # noqa: E402
from app.providers.gemini import (  # noqa: E402
    API_KEY_ENV,
    API_KEY_HEADER,
    API_URL_TEMPLATE,
    JSON_MIME_TYPE,
    _image_parts,
)
from app.providers.prompts import Prompts  # noqa: E402

BLANK_PAGE_PX = 200
MAX_PRINTED_CHARS = 2000


def _blank_page() -> PageImage:
    image = np.full((BLANK_PAGE_PX, BLANK_PAGE_PX, 3), 255, np.uint8)
    return PageImage(file_id="blank", page_no=0, image=image, original_path=Path("blank"), original=image)


def _send(client: httpx.Client, url: str, key: str, body: dict) -> None:
    started = time.monotonic()
    response = client.post(url, json=body, headers={API_KEY_HEADER: key, "content-type": JSON_MIME_TYPE})
    print(f"HTTP {response.status_code} in {time.monotonic() - started:.1f} s")
    print(response.text[:MAX_PRINTED_CHARS])


def main() -> None:
    key = os.environ.get(API_KEY_ENV, "")
    model = sys.argv[1] if len(sys.argv) > 1 else Settings().gemini_model
    url = API_URL_TEMPLATE.format(model=model)
    print(f"model={model} key_len={len(key)} url={url}")
    print("proxy env:", {k: v for k, v in os.environ.items() if "proxy" in k.lower()})
    with httpx.Client(timeout=Settings().gemini_timeout_s) as client:
        print("\n--- 1. text only ---")
        _send(client, url, key, {"contents": [{"parts": [{"text": "Say OK"}]}]})
        print("\n--- 2. same request as the provider (blank page) ---")
        _send(client, url, key, {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [*_image_parts([_blank_page().image]),
                                                   {"text": Prompts.load().extract(None)}]}],
            "generationConfig": {"responseMimeType": JSON_MIME_TYPE, "responseJsonSchema": FORM_READING_SCHEMA},
        })


if __name__ == "__main__":
    main()
