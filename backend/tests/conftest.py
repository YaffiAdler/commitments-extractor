import numpy as np
import pytest

from app.config import StorageName
from synthetic import variants

# API test modules that run once per job store, without any change to their code.
STORE_PARAMETRIZED_MODULES = frozenset({"test_e2e", "test_main"})


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if metafunc.module.__name__ in STORE_PARAMETRIZED_MODULES:
        names = [name.value for name in StorageName]
        metafunc.parametrize("job_store_backend", names, ids=names, indirect=True)


@pytest.fixture(autouse=True)
def job_store_backend(request, tmp_path_factory, monkeypatch) -> str:
    """Select the job store and keep its database out of the repository."""
    backend = getattr(request, "param", StorageName.SQLITE.value)
    monkeypatch.setenv("CE_STORAGE", backend)
    monkeypatch.setenv("CE_DB_PATH", str(tmp_path_factory.mktemp("db") / "app.db"))
    return backend


@pytest.fixture(scope="session")
def dummy_form() -> np.ndarray:
    """A form-like page with dummy values (see scripts/make_synthetic_variants.py)."""
    return variants.dummy_form()
