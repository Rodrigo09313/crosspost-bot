from __future__ import annotations

import logging

import pytest

from app.logging_setup import SecretMaskFilter


def test_secrets_masked_in_message_args_and_traceback(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.mask")
    caplog.handler.addFilter(SecretMaskFilter(["123:SECRET", "green-token"]))
    with caplog.at_level(logging.INFO, logger="test.mask"):
        logger.info("url https://api.telegram.org/bot%s/getMe", "123:SECRET")
        try:
            raise RuntimeError("fail for green-token")
        except RuntimeError:
            logger.exception("ошибка")
    assert "SECRET" not in caplog.text and "green-token" not in caplog.text
    assert "bot***/getMe" in caplog.text
