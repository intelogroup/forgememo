"""
Tests for exit_code parsing and error detection in hook.py.
"""


from forgememo.hook import (
    _extract_error_text,
    _error_fingerprint,
    _ERROR_PATTERNS,
    _parse_exit_code,
)


def _make_payload(**kwargs) -> dict:
    """Create a payload dict that _extract_error_text expects."""
    return {"tool_response": kwargs}


class TestParseExitCode:
    """Test the _parse_exit_code helper function."""

    def test_numeric_zero_returns_not_error(self):
        """Exit code 0 returns (0, False, False)."""
        val, is_error, is_cancelled = _parse_exit_code(0)
        assert val == 0
        assert is_error is False
        assert is_cancelled is False

    def test_numeric_nonzero_returns_error(self):
        """Non-zero exit code returns (n, True, False)."""
        val, is_error, is_cancelled = _parse_exit_code(127)
        assert val == 127
        assert is_error is True
        assert is_cancelled is False

    def test_string_numeric_zero(self):
        """String '0' returns (0, False, False)."""
        val, is_error, is_cancelled = _parse_exit_code("0")
        assert val == 0
        assert is_error is False
        assert is_cancelled is False

    def test_string_numeric_nonzero(self):
        """String '1' returns (1, True, False)."""
        val, is_error, is_cancelled = _parse_exit_code("1")
        assert val == 1
        assert is_error is True
        assert is_cancelled is False

    def test_sigint_is_cancellation(self):
        """SIGINT is recognized as cancellation."""
        val, is_error, is_cancelled = _parse_exit_code("SIGINT")
        assert val is None
        assert is_error is True
        assert is_cancelled is True

    def test_sigterm_is_error(self):
        """SIGTERM is an error (termination request)."""
        val, is_error, is_cancelled = _parse_exit_code("SIGTERM")
        assert val is None
        assert is_error is True
        assert is_cancelled is False

    def test_sigkill_is_error_not_cancellation(self):
        """SIGKILL is error but not cancellation (forced kill)."""
        val, is_error, is_cancelled = _parse_exit_code("SIGKILL")
        assert val is None
        assert is_error is True
        assert is_cancelled is False

    def test_cancelled_string(self):
        """'cancelled' is recognized as cancellation."""
        val, is_error, is_cancelled = _parse_exit_code("cancelled")
        assert val is None
        assert is_error is True
        assert is_cancelled is True

    def test_keyboard_interrupt(self):
        """'KeyboardInterrupt' is recognized as cancellation."""
        val, is_error, is_cancelled = _parse_exit_code("KeyboardInterrupt")
        assert val is None
        assert is_error is True
        assert is_cancelled is True

    def test_unknown_string_is_error(self):
        """Unknown strings are treated as errors."""
        val, is_error, is_cancelled = _parse_exit_code("0x1")
        assert val is None
        assert is_error is True
        assert is_cancelled is False

    def test_negative_exit_code(self):
        """Negative exit codes (signals) are parsed."""
        val, is_error, is_cancelled = _parse_exit_code("-15")
        assert val == -15
        assert is_error is True

    def test_none_returns_zeros(self):
        """None returns (None, False, False)."""
        val, is_error, is_cancelled = _parse_exit_code(None)
        assert val is None
        assert is_error is False
        assert is_cancelled is False

    def test_empty_string(self):
        """Empty string is treated as error."""
        val, is_error, is_cancelled = _parse_exit_code("")
        assert val is None
        assert is_error is True


class TestExitCodeParsing:
    """Test exit_code extraction and handling in _extract_error_text()."""

    def test_numeric_exit_code_nonzero_is_error(self):
        """Non-zero exit code triggers error with 'exit code N'."""
        result = _extract_error_text(
            _make_payload(exitCode=1, error="Something went wrong")
        )
        assert result is not None
        assert "exit code 1" in result

    def test_string_exit_code_numeric_nonzero_is_error(self):
        """String '127' exit code triggers error."""
        result = _extract_error_text(_make_payload(exitCode="127"))
        assert result is not None
        assert "exit code 127" in result

    def test_exit_code_with_leading_zeros(self):
        """Exit code '001' is parsed as integer 1."""
        result = _extract_error_text(_make_payload(exitCode="001"))
        assert "exit code 1" in result

    def test_exit_code_sigint_is_cancelled(self):
        """SIGINT is user cancellation — not reported as an error event."""
        result = _extract_error_text(_make_payload(exitCode="SIGINT"))
        assert result is None

    def test_exit_code_sigterm_handled(self):
        """SIGTERM signal is recognized."""
        result = _extract_error_text(_make_payload(exitCode="SIGTERM"))
        assert result is not None
        assert "exit code SIGTERM" in result

    def test_exit_code_empty_string(self):
        """Empty string exit code is handled gracefully."""
        result = _extract_error_text(_make_payload(exitCode=""))
        assert result is None

    def test_exit_code_snake_case(self):
        """exit_code (snake_case) is also accepted."""
        result = _extract_error_text(_make_payload(exit_code=1))
        assert result is not None
        assert "exit code 1" in result

    def test_exit_code_precedence(self):
        """exitCode takes precedence over exit_code."""
        result = _extract_error_text(_make_payload(exitCode=1, exit_code=2))
        assert "exit code 1" in result
        assert "exit code 2" not in result


class TestInterruptedField:
    """Test 'interrupted' field handling."""

    def test_interrupted_true_adds_marker(self):
        """'interrupted: true' adds 'command interrupted'."""
        result = _extract_error_text(_make_payload(interrupted=True))
        assert result is not None
        assert "command interrupted" in result

    def test_interrupted_false_no_marker(self):
        """'interrupted: false' does not add marker."""
        result = _extract_error_text(_make_payload(interrupted=False))
        assert result is None


class TestErrorPatterns:
    """Test _ERROR_PATTERNS regex matching."""

    def test_connection_error_matches(self):
        """ConnectionError matches error pattern."""
        text = "ConnectionError: Failed to connect"
        assert _ERROR_PATTERNS.search(text) is not None

    def test_permission_error_matches(self):
        """PermissionError matches."""
        text = "PermissionError: Access denied"
        assert _ERROR_PATTERNS.search(text) is not None

    def test_file_not_found_matches(self):
        """FileNotFoundError matches."""
        text = "FileNotFoundError: [Errno 2] No such file"
        assert _ERROR_PATTERNS.search(text) is not None

    def test_traceback_matches(self):
        """Traceback lines match."""
        text = 'Traceback (most recent call last):\n  File "test.py", line 1'
        assert _ERROR_PATTERNS.search(text) is not None

    def test_npm_err_matches(self):
        """npm ERR! matches."""
        text = "npm ERR! code ENOENT"
        assert _ERROR_PATTERNS.search(text) is not None

    def test_non_error_text_no_match(self):
        """Normal output does not match."""
        text = "Successfully completed task"
        assert _ERROR_PATTERNS.search(text) is None


class TestErrorFingerprint:
    """Test _error_fingerprint function."""

    def test_strips_file_paths(self):
        """File paths are stripped from fingerprint."""
        text = "Error: /Users/Developer/Project/file.py line 10"
        fp = _error_fingerprint(text)
        assert "/Users/Developer/Project" not in fp

    def test_strips_line_numbers(self):
        """Line numbers are stripped from fingerprint."""
        text = "Error on line 12345"
        fp = _error_fingerprint(text)
        assert "12345" not in fp

    def test_handles_multiline(self):
        """Multiple error lines are handled."""
        text = "ConnectionError: Failed\nTimeoutError: Request timed out"
        fp = _error_fingerprint(text)
        assert fp is not None

    def test_truncates_to_three_lines(self):
        """Fingerprint is limited to 3 key lines."""
        lines = [f"Error line {i}" for i in range(10)]
        text = "\n".join(lines)
        fp = _error_fingerprint(text)
        fp_lines = fp.strip().splitlines()
        assert len(fp_lines) <= 3


class TestExtractErrorTextIntegration:
    """Integration tests for _extract_error_text()."""

    def test_realistic_tool_error(self):
        """Test with realistic tool failure output."""
        result = _extract_error_text(
            _make_payload(
                toolName="Bash",
                exitCode=1,
                stderr="npm ERR! code ENOENT\nnpm ERR! syscall open",
                error="Command failed",
            )
        )
        assert result is not None
        assert "exit code 1" in result

    def test_tool_with_success_output(self):
        """Tool with exit code 0 and no errors returns None."""
        result = _extract_error_text(
            _make_payload(toolName="Read", exitCode=0, stdout="File contents here")
        )
        assert result is None

    def test_dict_result_with_error(self):
        """Dict result with error field uses error patterns."""
        result = _extract_error_text(_make_payload(error="ValueError: invalid literal"))
        assert result is not None

    def test_empty_payload(self):
        """Empty payload returns None."""
        result = _extract_error_text({})
        assert result is None
