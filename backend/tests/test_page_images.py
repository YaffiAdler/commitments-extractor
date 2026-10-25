from dataclasses import replace

import numpy as np
import pytest

from app.pipeline.ingest import PageImage
from app.storage.page_images import PAGES_DIR, PageImageError, PageImageStore
from app.storage.tmp import TempStorage

JOB_ID = "e" * 32
FILE_ID = "f" * 32 + ".pdf"


def _page(page_no: int = 0) -> PageImage:
    image = np.full((40, 30, 3), 200, np.uint8)
    image[10:20, 5:15] = 0
    return PageImage(file_id=FILE_ID, page_no=page_no, image=image, original_path=None, original=image)


def test_saved_page_is_loaded_back(tmp_path):
    store = PageImageStore(TempStorage(tmp_path))
    store.save(JOB_ID, _page(2))
    loaded = store.load(JOB_ID, FILE_ID, 2)
    assert loaded.shape == (40, 30, 3)
    assert abs(int(loaded[15, 10, 0]) - 0) < 10 and abs(int(loaded[30, 25, 0]) - 200) < 10  # JPEG, close enough
    assert (tmp_path / JOB_ID / PAGES_DIR).is_dir()


def test_missing_page_is_none(tmp_path):
    store = PageImageStore(TempStorage(tmp_path))
    assert store.load(JOB_ID, FILE_ID, 0) is None


@pytest.mark.parametrize(("source_file", "page"), [("../x.pdf", 0), ("name.pdf", 0), (FILE_ID, -1)])
def test_refuses_names_that_are_not_generated(tmp_path, source_file, page):
    with pytest.raises(ValueError):
        PageImageStore(TempStorage(tmp_path)).load(JOB_ID, source_file, page)


def test_page_goes_with_the_job_directory(tmp_path):
    storage = TempStorage(tmp_path)
    store = PageImageStore(storage)
    store.save(JOB_ID, _page())
    storage.delete_job(JOB_ID)
    assert store.load(JOB_ID, FILE_ID, 0) is None


def test_a_released_page_cannot_be_saved(tmp_path):
    page = _page()
    released = replace(page, ocr_image=np.zeros((40, 30), np.uint8)).without_original(lambda: None)
    with pytest.raises(PageImageError):
        PageImageStore(TempStorage(tmp_path)).save(JOB_ID, released)
