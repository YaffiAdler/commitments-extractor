import io

import numpy as np
import pytest
from PIL import Image

from app.field_crops import FIELD_NOT_FOUND, IMAGE_GONE, NO_BOX, CropUnavailableError, field_crop_png
from app.pipeline.ingest import PageImage
from app.schemas import BBox, Field, FormGroup
from app.storage.page_images import PageImageStore
from app.storage.tmp import TempStorage

JOB_ID = "e" * 32
FILE_ID = "f" * 32 + ".png"


@pytest.fixture
def images(tmp_path) -> PageImageStore:
    store = PageImageStore(TempStorage(tmp_path))
    image = np.full((200, 100, 3), 255, np.uint8)
    store.save(JOB_ID, PageImage(file_id=FILE_ID, page_no=0, image=image, original_path=None, original=image))
    return store


def _group(bbox: BBox | None) -> FormGroup:
    group = FormGroup(source_file=FILE_ID, pages=[0])
    group.commitment.id_number = Field(value="000000018", bbox=bbox)
    return group


def test_crop_is_the_padded_box(images):
    png = field_crop_png(images, JOB_ID, _group(BBox(page=0, x0=20, y0=30, x1=60, y1=50)), "id_number", None)
    assert Image.open(io.BytesIO(png)).size == (60, 40)


def test_a_name_crop_is_wider_than_its_box(images):
    box = BBox(page=0, x0=20, y0=30, x1=60, y1=50)
    group = _group(box)
    group.commitment.first_name = Field[str](value="דוד", bbox=box)
    name = Image.open(io.BytesIO(field_crop_png(images, JOB_ID, group, "first_name", None))).size
    number = Image.open(io.BytesIO(field_crop_png(images, JOB_ID, group, "id_number", None))).size
    assert name[0] > number[0]


@pytest.mark.parametrize(("bbox", "name", "code"), [
    (None, "id_number", NO_BOX),
    (BBox(page=0, x0=500, y0=500, x1=600, y1=600), "id_number", NO_BOX),
    (BBox(page=0, x0=1, y0=1, x1=40, y1=40), "central_numbers", FIELD_NOT_FOUND),
    (BBox(page=3, x0=1, y0=1, x1=40, y1=40), "id_number", IMAGE_GONE),
])
def test_unavailable_crops(images, bbox, name, code):
    with pytest.raises(CropUnavailableError) as raised:
        field_crop_png(images, JOB_ID, _group(bbox), name, None)
    assert str(raised.value) == code
