"""TesseractEngine with pytesseract mocked; one optional test with the real binary."""
import numpy as np
import pytesseract
import pytest

from app.config import Settings
from app.ocr import create_ocr_engine
from app.ocr.base import OcrError
from app.ocr.tesseract import TesseractEngine, TesseractUnavailableError

IMAGE = np.full((50, 50), 255, np.uint8)


def _languages(monkeypatch, langs):
    monkeypatch.setattr(pytesseract, "get_languages", lambda config="": langs)


def _not_found(config=""):
    raise pytesseract.TesseractNotFoundError()


def test_missing_binary(monkeypatch):
    monkeypatch.setattr(pytesseract, "get_languages", _not_found)
    with pytest.raises(TesseractUnavailableError):
        TesseractEngine.create("", "heb+eng", 5)
    assert create_ocr_engine(Settings()) is None


def test_missing_osd(monkeypatch):
    _languages(monkeypatch, ["heb", "eng"])
    with pytest.raises(TesseractUnavailableError):
        TesseractEngine.create("", "heb+eng", 5)


def test_missing_languages(monkeypatch):
    _languages(monkeypatch, ["osd", "fra"])
    with pytest.raises(TesseractUnavailableError):
        TesseractEngine.create("", "heb+eng", 5)


def test_keeps_installed_languages_only(monkeypatch):
    _languages(monkeypatch, ["osd", "eng"])
    captured = {}

    def image_to_data(image, lang, output_type, timeout):
        captured["lang"] = lang
        return {"text": ["", "ab", "x", "word"], "conf": ["-1", "90", "99", "50"]}

    monkeypatch.setattr(pytesseract, "image_to_data", image_to_data)
    engine = TesseractEngine.create("", "heb+eng", 5)
    # "x" is too short; "-1" marks a non-word box.
    assert engine.text_confidence(IMAGE) == pytest.approx(1.4)
    assert captured["lang"] == "eng"


def test_read_words_keeps_word_boxes(monkeypatch):
    data = {"text": ["", " אב ", "12"], "conf": ["-1", "90", "45.5"], "left": [0, 10, 40],
            "top": [0, 5, 5], "width": [50, 20, 15], "height": [50, 8, 8]}
    monkeypatch.setattr(pytesseract, "image_to_data", lambda image, lang, output_type, timeout: data)
    words = TesseractEngine("heb", 5).read_words(IMAGE)
    assert [(w.text, w.left, w.top) for w in words] == [("אב", 10, 5), ("12", 40, 5)]
    assert words[1].confidence == pytest.approx(0.455)


def test_osd_rotation_is_parsed(monkeypatch):
    monkeypatch.setattr(pytesseract, "image_to_osd",
                        lambda image, config, output_type, timeout: {"rotate": 270, "orientation_conf": 7.5})
    guess = TesseractEngine("eng", 5).detect_orientation(IMAGE)
    assert (guess.rotation, guess.confidence) == (270, 7.5)


def test_osd_failure_is_ocr_error(monkeypatch):
    def fail(image, config, output_type, timeout):
        raise pytesseract.TesseractError(1, "Too few characters")

    monkeypatch.setattr(pytesseract, "image_to_osd", fail)
    with pytest.raises(OcrError):
        TesseractEngine("eng", 5).detect_orientation(IMAGE)


def test_timeout_is_ocr_error(monkeypatch):
    def time_out(image, lang, output_type, timeout):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(pytesseract, "image_to_data", time_out)
    with pytest.raises(OcrError):
        TesseractEngine("eng", 5).text_confidence(IMAGE)


def test_real_tesseract_rotated_dummy_form(dummy_form):
    """Runs only where Tesseract is installed (e.g. the Docker image)."""
    engine = create_ocr_engine(Settings())
    if engine is None:
        pytest.skip("tesseract is not installed")
    import cv2
    from app.imaging.orientation import detect_orientation

    rotated = cv2.rotate(cv2.cvtColor(dummy_form, cv2.COLOR_BGR2GRAY), cv2.ROTATE_90_CLOCKWISE)
    assert detect_orientation(rotated, engine, Settings().preprocess.osd_min_confidence).rotation == 270
