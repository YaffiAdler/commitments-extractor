"""The crop of one field's box from its stored page image, for the reviewer
(``GET /jobs/{id}/groups/{group}/fields/{field}/crop``). The crop is made on
request from the page image in the job's temporary directory; it is never
written to disk or to the database."""
from __future__ import annotations

import cv2

from app.corrections import field_at
from app.pipeline.crops import crop_bbox
from app.schemas import FormGroup
from app.storage.page_images import PageImageStore

PNG_SUFFIX = ".png"


class CropUnavailableError(LookupError):
    """No crop for this field; the message is a fixed code, safe to return to the client."""


FIELD_NOT_FOUND = "field_not_found"
NO_BOX = "no_box"
# The page image was deleted (file TTL, job deleted, or a server restart on an ephemeral disk).
IMAGE_GONE = "image_gone"


def field_crop_png(images: PageImageStore, job_id: str, group: FormGroup, name: str, item: int | None) -> bytes:
    """The PNG of the field's box (plus the usual padding) on its page."""
    field = field_at(group, name, item)
    if field is None:
        raise CropUnavailableError(FIELD_NOT_FOUND)
    if field.bbox is None:
        raise CropUnavailableError(NO_BOX)
    page = images.load(job_id, group.source_file, field.bbox.page)
    if page is None:
        raise CropUnavailableError(IMAGE_GONE)
    crop = crop_bbox(page, field.bbox)
    if crop is None:
        raise CropUnavailableError(NO_BOX)
    ok, encoded = cv2.imencode(PNG_SUFFIX, crop)
    if not ok:
        raise ValueError("crop could not be encoded")
    return encoded.tobytes()
