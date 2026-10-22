import os
from uuid import uuid4

import numpy as np
import pytest

from app.config import DATABASE_URL_ENV, StorageName
from synthetic import variants

# API test modules that run once per job store, without any change to their code.
STORE_PARAMETRIZED_MODULES = frozenset({"test_e2e", "test_main"})
# Postgres tests run only when this points at a database the tests may create schemas in.
TEST_DATABASE_URL_ENV = "TEST_DATABASE_URL"
NO_TEST_DATABASE = f"{TEST_DATABASE_URL_ENV} is not set"


def store_params() -> list:
    """Every job store as a pytest param; postgres is skipped without a test database."""
    return [
        pytest.param(name.value, id=name.value,
                     marks=pytest.mark.skipif(name is StorageName.POSTGRES and not os.environ.get(TEST_DATABASE_URL_ENV),
                                              reason=NO_TEST_DATABASE))
        for name in StorageName
    ]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if metafunc.module.__name__ in STORE_PARAMETRIZED_MODULES:
        metafunc.parametrize("job_store_backend", store_params(), indirect=True)


@pytest.fixture
def pg_conninfo(monkeypatch):
    """A connection string to a fresh, empty schema of the test database (dropped
    afterwards). Every ``PostgresJobStore`` made during the test is closed at the end."""
    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not url:
        pytest.skip(NO_TEST_DATABASE)
    import psycopg
    from psycopg.conninfo import make_conninfo

    from app.storage.postgres import PostgresJobStore

    schema = f"ce_test_{uuid4().hex}"
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(f"CREATE SCHEMA {schema}")
    created: list[PostgresJobStore] = []
    original_init = PostgresJobStore.__init__

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(PostgresJobStore, "__init__", tracking_init)
    yield make_conninfo(url, options=f"-c search_path={schema}")
    for store in created:
        store.close()
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture(autouse=True)
def job_store_backend(request, tmp_path_factory, monkeypatch) -> str:
    """Select the job store and keep its database out of the repository."""
    backend = getattr(request, "param", StorageName.MEMORY.value)
    monkeypatch.setenv("CE_STORAGE", backend)
    monkeypatch.setenv("CE_DB_PATH", str(tmp_path_factory.mktemp("db") / "app.db"))
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    if backend == StorageName.POSTGRES.value:
        monkeypatch.setenv(DATABASE_URL_ENV, request.getfixturevalue("pg_conninfo"))
    return backend


@pytest.fixture(scope="session")
def dummy_form() -> np.ndarray:
    """A form-like page with dummy values (see scripts/make_synthetic_variants.py)."""
    return variants.dummy_form()
