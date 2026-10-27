from form_fakes import blank_page
from app.pipeline.crops import CROP_PADDING_PX, crop_field, digits_of, is_number_text, numeric_fields
from app.schemas import BBox, Commitment, Field


def test_numeric_fields_in_check_order():
    commitment = Commitment(id_number=Field[str](value="1"), central_numbers=[Field[str](value="2")],
                            commitment_numbers=[Field[str](value="3"), Field[str](value="4")])
    assert [f.value for f in numeric_fields(commitment)] == ["1", "2", "3", "4"]


def test_number_text():
    assert digits_of("12-34 5") == "12345" and digits_of(None) == ""
    assert is_number_text("12.34/5") and not is_number_text("12AB") and not is_number_text("--")


def test_crop_field_uses_the_page_of_the_box():
    pages = [blank_page(3), blank_page(4)]
    bbox = BBox(page=4, x0=100, y0=100, x1=300, y1=140)
    assert crop_field(bbox, pages, for_ocr=True).shape == (40 + 2 * CROP_PADDING_PX, 200 + 2 * CROP_PADDING_PX)
    assert crop_field(bbox, pages, for_ocr=False).ndim == 3  # colour for the model
    assert crop_field(BBox(page=9, x0=0, y0=0, x1=5, y1=5), pages, for_ocr=True) is None


def test_crop_outside_or_too_small_is_none():
    pages = [blank_page()]
    assert crop_field(BBox(page=0, x0=5000, y0=10, x1=5100, y1=20), pages, for_ocr=True) is None
    assert crop_field(BBox(page=0, x0=0, y0=0, x1=0, y1=0), pages, for_ocr=True) is None


def test_colour_crop_of_a_released_page_is_read_back():
    page = blank_page(0)
    stored = page.original
    bbox = BBox(page=0, x0=100, y0=100, x1=300, y1=140)
    assert crop_field(bbox, [page.without_original(lambda: stored)], for_ocr=False).ndim == 3
    assert crop_field(bbox, [page.without_original(lambda: None)], for_ocr=False) is None
    assert crop_field(bbox, [page.without_original(lambda: None)], for_ocr=True) is not None  # ocr_image in memory


def test_crop_padding_can_be_set():
    bbox = BBox(page=0, x0=100, y0=100, x1=300, y1=140)
    assert crop_field(bbox, [blank_page()], for_ocr=False, padding_px=0).shape[:2] == (40, 200)
    assert crop_field(bbox, [blank_page()], for_ocr=False, padding_px=30).shape[:2] == (100, 260)
