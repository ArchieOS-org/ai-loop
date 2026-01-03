"""Tests for input sanitization."""

import pytest

from ai_loop.safety.sanitizer import (
    MAX_CONTENT_LENGTH,
    escape_for_shell,
    is_safe_path,
    sanitize_issue_content,
    sanitize_issue_title,
)


class TestSanitizeIssueContent:
    """Tests for sanitize_issue_content."""

    def test_empty_content(self):
        assert sanitize_issue_content(None) == ""
        assert sanitize_issue_content("") == ""

    def test_normal_content_unchanged(self):
        content = "This is a normal issue description with no special characters."
        assert sanitize_issue_content(content) == content

    def test_truncation(self):
        long_content = "x" * (MAX_CONTENT_LENGTH + 1000)
        result = sanitize_issue_content(long_content)
        assert len(result) <= MAX_CONTENT_LENGTH + 20  # Allow for [TRUNCATED]
        assert "[TRUNCATED]" in result

    def test_shell_injection_subshell(self):
        content = "Run this: $(rm -rf /)"
        result = sanitize_issue_content(content)
        assert "$(rm -rf /)" not in result
        assert "[FILTERED" in result

    def test_shell_injection_backtick(self):
        content = "Execute `whoami` to get user"
        result = sanitize_issue_content(content)
        assert "`whoami`" not in result
        assert "[FILTERED" in result

    def test_shell_injection_pipe(self):
        content = "Check output | cat /etc/passwd"
        result = sanitize_issue_content(content)
        assert "| cat" not in result
        assert "[FILTERED" in result

    def test_path_traversal(self):
        content = "Open file at ../../../etc/passwd"
        result = sanitize_issue_content(content)
        assert "../" not in result
        assert "[FILTERED" in result

    def test_env_variable_expansion(self):
        content = "Use ${HOME}/config"
        result = sanitize_issue_content(content)
        assert "${HOME}" not in result
        assert "[FILTERED" in result

    def test_script_tag(self):
        content = "<script>alert('xss')</script>"
        result = sanitize_issue_content(content)
        assert "<script" not in result.lower()
        assert "[FILTERED" in result

    def test_null_bytes(self):
        content = "Normal text\x00hidden"
        result = sanitize_issue_content(content)
        assert "\x00" not in result

    def test_preserves_markdown(self):
        content = """# Title

- Item 1
- Item 2

```python
def foo():
    pass
```
"""
        result = sanitize_issue_content(content)
        assert "# Title" in result
        assert "```python" in result


class TestSanitizeIssueTitle:
    """Tests for sanitize_issue_title."""

    def test_normal_title(self):
        title = "Fix login button styling"
        assert sanitize_issue_title(title) == title

    def test_truncation(self):
        long_title = "x" * 300
        result = sanitize_issue_title(long_title)
        assert len(result) <= 203  # 200 + "..."

    def test_removes_newlines(self):
        title = "First line\nSecond line\rThird"
        result = sanitize_issue_title(title)
        assert "\n" not in result
        assert "\r" not in result

    def test_injection_filtered(self):
        title = "Title $(whoami)"
        result = sanitize_issue_title(title)
        assert "$(whoami)" not in result


class TestEscapeForShell:
    """Tests for escape_for_shell."""

    def test_normal_string(self):
        result = escape_for_shell("hello world")
        assert result == "'hello world'"

    def test_single_quotes(self):
        result = escape_for_shell("it's working")
        assert "'" in result
        # Should escape single quotes properly
        assert result == "'it'\"'\"'s working'"

    def test_empty_string(self):
        result = escape_for_shell("")
        assert result == "''"


class TestIsSafePath:
    """Tests for is_safe_path."""

    def test_path_within_root(self):
        assert is_safe_path("/home/user/project/file.txt", "/home/user/project")

    def test_path_outside_root(self):
        assert not is_safe_path("/etc/passwd", "/home/user/project")

    def test_path_traversal_attempt(self):
        assert not is_safe_path("/home/user/project/../../../etc/passwd", "/home/user/project")

    def test_subdirectory(self):
        assert is_safe_path("/home/user/project/src/utils/file.ts", "/home/user/project")
