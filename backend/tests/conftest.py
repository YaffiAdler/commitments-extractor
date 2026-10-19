import numpy as np
import pytest

from synthetic import variants


@pytest.fixture(scope="session")
def dummy_form() -> np.ndarray:
    """A form-like page with dummy values (see scripts/make_synthetic_variants.py)."""
    return variants.dummy_form()
