import logging

import pytest
from click.testing import CliRunner

import eb_tools
from eb_tools.cli import main
from eb_tools.utils import setup_logging


def test_version():
    # 版本由 git tag 派生（hatch-vcs），本地未打 tag 时为 0.1.dev* 形式
    assert eb_tools.__version__
    assert eb_tools.__version__ != "0.0.0"


def test_main_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"eb, version {eb_tools.__version__}"


def test_setup_logging_applies_level_and_format():
    logger = setup_logging("test-logger", log_level="debug")
    assert logger.level == logging.DEBUG
    assert logger.handlers[0].formatter._fmt == "%(asctime)s %(levelname)s: %(message)s"


def test_setup_logging_rejects_unknown_level():
    with pytest.raises(ValueError, match="invalid log level"):
        setup_logging("test-logger", log_level="nope")
