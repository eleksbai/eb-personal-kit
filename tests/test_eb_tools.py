from click.testing import CliRunner

import eb_tools
from eb_tools.cli import get_version, main


def test_version():
    assert eb_tools.__version__ == "0.1.0"


def test_get_version_matches_package():
    assert get_version() == "0.1.0"


def test_main_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == "eb, version 0.1.0"
