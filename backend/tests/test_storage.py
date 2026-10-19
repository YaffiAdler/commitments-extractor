import time
from uuid import uuid4

import pytest

from app.storage.tmp import TempStorage


def test_save_and_delete(tmp_path):
    storage = TempStorage(tmp_path)
    job_id = uuid4().hex
    path = storage.save_upload(job_id, b"%PDF-1.4")
    assert path.read_bytes() == b"%PDF-1.4"
    assert path.parent == storage.job_dir(job_id)
    storage.delete_job(job_id)
    assert not storage.job_dir(job_id).exists()


@pytest.mark.parametrize("job_id", ["../escape", "", "ABC"])
def test_rejects_unsafe_job_id(tmp_path, job_id):
    with pytest.raises(ValueError):
        TempStorage(tmp_path).job_dir(job_id)


def test_purge_older_than(tmp_path):
    storage = TempStorage(tmp_path)
    job_id = uuid4().hex
    storage.save_upload(job_id, b"%PDF-")
    other = tmp_path / "not-a-job"
    other.mkdir()
    assert storage.purge_older_than(60) == []
    assert storage.purge_older_than(60, now=time.time() + 120) == [job_id]
    assert not storage.job_dir(job_id).exists()
    assert other.exists()


def test_purge_missing_root(tmp_path):
    assert TempStorage(tmp_path / "missing").purge_older_than(0) == []
