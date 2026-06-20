"""Tests for piazza CLI."""

import subprocess
import sys

import pytest

from piazza.cli import _redact_token, parse_args


class TestParseArgs:
    """Tests for CLI argument parsing."""

    def test_defaults(self):
        args = parse_args([])
        assert args.db == "piazza.db"
        assert args.host == "127.0.0.1"
        assert args.port == 8741
        assert args.remote is False
        assert args.token is None
        assert args.no_ui is False
        assert args.log_level == "INFO"

    def test_custom_db(self):
        args = parse_args(["-d", "custom.db"])
        assert args.db == "custom.db"

    def test_custom_host_port(self):
        args = parse_args(["-H", "0.0.0.0", "-p", "9000"])
        assert args.host == "0.0.0.0"
        assert args.port == 9000

    def test_remote_flag(self):
        args = parse_args(["--remote"])
        assert args.remote is True

    def test_token(self):
        args = parse_args(["--token", "my-secret"])
        assert args.token == "my-secret"

    def test_no_ui(self):
        args = parse_args(["--no-ui"])
        assert args.no_ui is True

    def test_log_level(self):
        args = parse_args(["--log-level", "DEBUG"])
        assert args.log_level == "DEBUG"

    def test_invalid_log_level(self):
        with pytest.raises(SystemExit):
            parse_args(["--log-level", "TRACE"])

    def test_invalid_port(self):
        with pytest.raises(SystemExit):
            parse_args(["-p", "not_a_number"])


class TestRedactToken:
    """Tests for token redaction."""

    def test_long_token(self):
        token = "sk-abcdef1234567890abcdef1234567890abcdef1234567890"
        result = _redact_token(token)
        assert result.startswith("sk-abcde")
        assert result.endswith("7890")
        assert "..." in result
        assert len(result) < len(token)

    def test_short_token(self):
        assert _redact_token("short") == "short"

    def test_exact_boundary(self):
        token = "a" * 12  # head=8 + tail=4 = 12
        assert _redact_token(token) == token

    def test_one_over_boundary(self):
        token = "a" * 13
        result = _redact_token(token)
        assert result == "aaaaaaaa...aaaa"

    def test_custom_head_tail(self):
        result = _redact_token("abcdefghij", head=3, tail=2)
        assert result == "abc...ij"


class TestCliVersion:
    """Test CLI version output via subprocess."""

    def test_version_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "piazza.cli", "--version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "piazza" in result.stdout

    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "piazza.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--port" in result.stdout
        assert "--remote" in result.stdout
