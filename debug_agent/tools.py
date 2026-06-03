"""File and code analysis tools for the debug agent."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


class FileTools:
    """Utility tools for reading source files and searching code."""

    def read_file(self, path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        """
        Read a source file, optionally restricted to a line range.

        Args:
            path: Absolute or relative path to the file.
            start_line: 1-based first line to include (inclusive). If omitted, read from line 1.
            end_line: 1-based last line to include (inclusive). If omitted, read to end of file.

        Returns:
            File contents as a string, with line numbers prefixed.
            Returns an error string if the file cannot be read.
        """
        file_path = Path(path)
        if not file_path.exists():
            return f"[error] File not found: {path}"
        if not file_path.is_file():
            return f"[error] Path is not a file: {path}"

        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return f"[error] Cannot read file {path}: {exc}"

        total = len(lines)
        lo = max(1, start_line) if start_line is not None else 1
        hi = min(total, end_line) if end_line is not None else total

        selected = lines[lo - 1 : hi]
        numbered = [f"{lo + i:>6} | {line}" for i, line in enumerate(selected)]
        header = f"# {path}  (lines {lo}-{hi} of {total})"
        return "\n".join([header] + numbered)

    def search_code(self, pattern: str, directory: str) -> str:
        """
        Recursively search for a regex pattern in source files under *directory*.

        Uses ``grep -rn`` so the pattern follows POSIX extended-regex syntax.
        Binary files and hidden directories are skipped.

        Args:
            pattern: Regular expression to search for.
            directory: Root directory to search under.

        Returns:
            Grep output (file:line:match) or an informational/error string.
        """
        dir_path = Path(directory)
        if not dir_path.exists():
            return f"[error] Directory not found: {directory}"
        if not dir_path.is_dir():
            return f"[error] Path is not a directory: {directory}"

        try:
            result = subprocess.run(
                [
                    "grep",
                    "--color=never",
                    "-rn",
                    "--include=*.py",
                    "--include=*.js",
                    "--include=*.ts",
                    "--include=*.java",
                    "--include=*.go",
                    "--include=*.rb",
                    "--include=*.cpp",
                    "--include=*.c",
                    "--include=*.cs",
                    "--include=*.rs",
                    "-E",
                    pattern,
                    str(dir_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            return "[error] grep not found. Install grep or use a POSIX-compatible system."
        except subprocess.TimeoutExpired:
            return f"[error] Search timed out after 30 seconds."

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 1 and not stdout:
            return f"[no matches] Pattern '{pattern}' not found under {directory}."
        if result.returncode > 1:
            return f"[error] grep returned exit code {result.returncode}: {stderr}"

        lines = stdout.splitlines()
        if len(lines) > 200:
            truncated = len(lines) - 200
            lines = lines[:200]
            lines.append(f"... ({truncated} more matches truncated)")
        return "\n".join(lines)

    def get_git_blame(self, file: str, line: int) -> str:
        """
        Return ``git blame`` output for a single line in *file*.

        This reveals the commit, author, and timestamp that last changed the
        line — useful context when tracking down regressions.

        Args:
            file: Path to the source file.
            line: 1-based line number to blame.

        Returns:
            Blame information string, or an error string if unavailable.
        """
        file_path = Path(file)
        if not file_path.exists():
            return f"[error] File not found: {file}"

        try:
            result = subprocess.run(
                [
                    "git",
                    "blame",
                    "-L",
                    f"{line},{line}",
                    "--porcelain",
                    str(file_path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=file_path.parent,
            )
        except FileNotFoundError:
            return "[error] git not found. Install git to use blame."
        except subprocess.TimeoutExpired:
            return "[error] git blame timed out."

        if result.returncode != 0:
            stderr = result.stderr.strip()
            return f"[error] git blame failed: {stderr}"

        output = result.stdout.strip()
        if not output:
            return f"[no blame data] Line {line} in {file} has no git history."
        return output
