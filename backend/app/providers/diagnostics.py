"""Diagnostics for provider calls (``CE_DEBUG_PROVIDER=1``).

Everything here describes the *shape* of a request or a failure: exception
types, sizes, image dimensions and media types. Never field values, prompt or
answer text, or the API key.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

import cv2
import numpy as np

EXCEPTION_CHAIN_SEPARATOR = " <- "
UNKNOWN_MEDIA_TYPE = "unknown"
# Leading bytes of the image formats an API may receive.
_MAGIC_BYTES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF8", "image/gif"),
    (b"RIFF", "image/webp"),
)


def exception_chain(exc: BaseException) -> str:
    """The types of ``exc`` and the exceptions it was raised from, outermost
    first (``__cause__``, else an unsuppressed ``__context__``). Types only:
    messages may quote values."""
    names: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names.append(type(current).__name__)
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return EXCEPTION_CHAIN_SEPARATOR.join(names)


def sniff_media_type(data: bytes) -> str:
    """The image format the bytes actually are, from their magic bytes."""
    for magic, media_type in _MAGIC_BYTES:
        if data.startswith(magic):
            return media_type
    return UNKNOWN_MEDIA_TYPE


@dataclass(frozen=True)
class ImageInfo:
    """What one base64 image block really contains."""

    declared_media_type: str
    actual_media_type: str
    base64_valid: bool
    encoded_bytes: int
    width: int
    height: int

    def describe(self) -> str:
        return (f"{self.width}x{self.height} {self.declared_media_type}"
                f"(actual {self.actual_media_type}) {self.encoded_bytes}B b64ok={self.base64_valid}")


def inspect_base64_image(data: str, declared_media_type: str) -> ImageInfo:
    """Decode a base64 image as the API would and report its real format and size."""
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return ImageInfo(declared_media_type, UNKNOWN_MEDIA_TYPE, False, len(data), 0, 0)
    decoded = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
    height, width = decoded.shape[:2] if decoded is not None else (0, 0)
    return ImageInfo(declared_media_type, sniff_media_type(raw), True, len(data), width, height)
