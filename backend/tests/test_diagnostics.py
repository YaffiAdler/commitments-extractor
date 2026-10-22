"""Provider diagnostics: exception chains and what an image block really holds."""
import base64

import cv2
import numpy as np

from app.providers.base import ProviderError, RetryableProviderError
from app.providers.common import JPEG_MEDIA_TYPE
from app.providers.diagnostics import exception_chain, inspect_base64_image, sniff_media_type


def _raise_chained() -> None:
    try:
        raise TimeoutError("dummy value 123")
    except TimeoutError as exc:
        raise RetryableProviderError("timed out") from exc


def test_exception_chain_follows_cause_with_types_only():
    try:
        _raise_chained()
    except RetryableProviderError as exc:
        chain = exception_chain(exc)
    assert chain == "RetryableProviderError <- TimeoutError"
    assert "123" not in chain


def test_exception_chain_stops_at_suppressed_context():
    try:
        try:
            raise ValueError("dummy")
        except ValueError:
            raise ProviderError("bad answer") from None
    except ProviderError as exc:
        assert exception_chain(exc) == "ProviderError"


def _encoded(extension: str) -> bytes:
    ok, data = cv2.imencode(extension, np.zeros((30, 40, 3), np.uint8))
    assert ok
    return data.tobytes()


def test_sniff_media_type_tells_png_from_jpeg():
    assert sniff_media_type(_encoded(".jpg")) == "image/jpeg"
    assert sniff_media_type(_encoded(".png")) == "image/png"
    assert sniff_media_type(b"not an image") == "unknown"


def test_inspect_reports_png_labelled_as_jpeg_and_its_size():
    info = inspect_base64_image(base64.b64encode(_encoded(".png")).decode(), JPEG_MEDIA_TYPE)
    assert (info.declared_media_type, info.actual_media_type) == ("image/jpeg", "image/png")
    assert (info.width, info.height, info.base64_valid) == (40, 30, True)


def test_inspect_flags_invalid_base64():
    info = inspect_base64_image("not base64!", JPEG_MEDIA_TYPE)
    assert not info.base64_valid
