import logging

from app.log_filters import ACCESS_LOGGER, RedactQueryTokenFilter, install_token_redaction, redact_token

TOKEN = "dummy-demo-token-0123456789"


def test_redact_token():
    assert redact_token(f"/jobs/1/events?access_token={TOKEN}") == "/jobs/1/events?access_token=***"
    assert redact_token(f"/x?item=2&access_token={TOKEN}&y=1") == "/x?item=2&access_token=***&y=1"
    assert redact_token("/jobs/1") == "/jobs/1"


def test_access_log_record_is_redacted():
    # uvicorn's access log: '%s - "%s %s HTTP/%s" %d' with the path as an argument.
    record = logging.LogRecord(ACCESS_LOGGER, logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:5000", "GET", f"/jobs/1/events?access_token={TOKEN}", "1.1", 200), None)
    assert RedactQueryTokenFilter().filter(record)
    assert TOKEN not in record.getMessage()
    assert "access_token=***" in record.getMessage()


def test_install_once():
    install_token_redaction()
    install_token_redaction()
    filters = [f for f in logging.getLogger(ACCESS_LOGGER).filters if isinstance(f, RedactQueryTokenFilter)]
    assert len(filters) == 1
