"""DebugAgent: uses Claude to perform root cause analysis on stack traces."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import anthropic

from .tools import FileTools

# Tool definitions exposed to Claude.
_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": (
            "Read a source file (or a line range within it). "
            "Use this to inspect the code mentioned in the stack trace."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the source file.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "1-based first line to read (inclusive). Omit to start from line 1.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "1-based last line to read (inclusive). Omit to read to end of file.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_code",
        "description": (
            "Search for a regex pattern in all source files under a directory. "
            "Useful for locating function definitions, variable assignments, or "
            "import statements referenced in the trace."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "POSIX extended-regex pattern to search for.",
                },
                "directory": {
                    "type": "string",
                    "description": "Root directory to search recursively.",
                },
            },
            "required": ["pattern", "directory"],
        },
    },
    {
        "name": "fetch_error_docs",
        "description": (
            "Return built-in reference documentation and common causes for a known "
            "error type (e.g. 'KeyError', 'NullPointerException', 'TypeError', "
            "'IndexError', 'AttributeError', 'ImportError', 'ValueError'). "
            "Call this early to ground the analysis in error semantics."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "error_type": {
                    "type": "string",
                    "description": "The exception/error class name, e.g. 'KeyError'.",
                },
            },
            "required": ["error_type"],
        },
    },
]

# Lightweight in-process error reference database.
_ERROR_DOCS: dict[str, str] = {
    "KeyError": (
        "**KeyError** (Python)\n"
        "Raised when a dictionary key is not found.\n\n"
        "Common causes:\n"
        "- Accessing `d[key]` when `key` was never inserted.\n"
        "- Typo in the key string or case mismatch.\n"
        "- Key deleted earlier in the same code path.\n"
        "- Dict populated from external data (JSON, CSV) that omits the key.\n\n"
        "Fix patterns: use `d.get(key, default)`, `key in d` guard, or `setdefault`."
    ),
    "AttributeError": (
        "**AttributeError** (Python)\n"
        "Raised when an attribute reference or assignment fails.\n\n"
        "Common causes:\n"
        "- Calling a method on `None` (object was not initialised).\n"
        "- Misspelled attribute/method name.\n"
        "- Using the wrong object type (e.g. treating a list as a dict).\n"
        "- Missing `__init__` assignment for an instance variable.\n\n"
        "Fix patterns: add `None` checks, use `hasattr`, verify the type with `isinstance`."
    ),
    "TypeError": (
        "**TypeError** (Python)\n"
        "Raised when an operation is applied to an object of inappropriate type.\n\n"
        "Common causes:\n"
        "- Wrong number of arguments to a function.\n"
        "- Mixing incompatible types (e.g. `str + int`).\n"
        "- Iterating a non-iterable.\n"
        "- Calling a non-callable.\n\n"
        "Fix patterns: add type annotations, use `isinstance` guards, validate inputs."
    ),
    "IndexError": (
        "**IndexError** (Python)\n"
        "Raised when a sequence subscript is out of range.\n\n"
        "Common causes:\n"
        "- Off-by-one errors (e.g. iterating with `range(len(lst))` and going one past).\n"
        "- Empty list/tuple.\n"
        "- Hard-coded index that no longer matches list size.\n\n"
        "Fix patterns: bounds-check with `len()`, use `enumerate`, use `.get` on dicts."
    ),
    "ValueError": (
        "**ValueError** (Python)\n"
        "Raised when a built-in or standard operation receives an argument of the right "
        "type but an inappropriate value.\n\n"
        "Common causes:\n"
        "- `int('abc')` — non-numeric string.\n"
        "- Unpacking the wrong number of elements.\n"
        "- Invalid enum member.\n\n"
        "Fix patterns: validate/sanitise input before conversion, use try/except."
    ),
    "ImportError": (
        "**ImportError / ModuleNotFoundError** (Python)\n"
        "Raised when an import statement fails to find or load the module.\n\n"
        "Common causes:\n"
        "- Package not installed in the current virtual environment.\n"
        "- Wrong package name (use pip install, not module name).\n"
        "- Circular import between modules.\n"
        "- `sys.path` does not include the package root.\n\n"
        "Fix patterns: `pip install <package>`, verify `sys.path`, restructure to break circles."
    ),
    "NullPointerException": (
        "**NullPointerException** (Java)\n"
        "Thrown when the application attempts to use null where an object is required.\n\n"
        "Common causes:\n"
        "- Method called on an uninitialised field.\n"
        "- Return value not checked for null before use.\n"
        "- Array element not initialised.\n\n"
        "Fix patterns: null checks, Optional<T>, Objects.requireNonNull, initialise in constructor."
    ),
    "RuntimeError": (
        "**RuntimeError** (Python)\n"
        "Raised when an error is detected that does not fall under any other category.\n\n"
        "Common causes:\n"
        "- Recursion limit exceeded.\n"
        "- Generator already running (re-entrant call).\n"
        "- Superclass __init__ not called.\n\n"
        "Fix patterns: investigate the message text for the specific sub-issue."
    ),
    "PermissionError": (
        "**PermissionError** (Python / OS)\n"
        "Raised when trying to run an operation without adequate access rights.\n\n"
        "Common causes:\n"
        "- Writing to a read-only file or directory.\n"
        "- Running without root/admin when required.\n"
        "- File locked by another process.\n\n"
        "Fix patterns: check file permissions, run with elevated privileges, use a different path."
    ),
    "FileNotFoundError": (
        "**FileNotFoundError** (Python)\n"
        "Raised when a file or directory is requested but does not exist.\n\n"
        "Common causes:\n"
        "- Hardcoded path that differs between environments.\n"
        "- Working directory assumption wrong.\n"
        "- File deleted or never created.\n\n"
        "Fix patterns: use `Path(__file__).parent` for relative paths, `os.path.exists` guard."
    ),
}


class DebugAgent:
    """
    An AI agent that reads a stack trace plus source code and produces a
    structured root cause analysis with a suggested fix.

    The agent runs a multi-step agentic loop:
    1. Parse the stack trace to identify the error type and relevant files.
    2. Read those source files around the failing lines.
    3. Optionally search for definitions or related code.
    4. Synthesise a root cause and suggest a concrete fix.
    """

    MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 4096
    MAX_LOOP_ITERATIONS = 20  # safeguard against infinite loops

    SYSTEM_PROMPT = """You are an expert software debugger. Your task is to perform
root cause analysis on a stack trace.

You have three tools available:
- read_file: read the contents of a source file (optionally a line range)
- search_code: grep for a pattern across source files in a directory
- fetch_error_docs: retrieve reference documentation for a known error type

Analysis process:
1. Call fetch_error_docs for the error class to ground your analysis.
2. Identify every file and line number referenced in the stack trace.
3. Call read_file for the most relevant frames, adding a few lines of context
   around each failing line.
4. If definitions or usage patterns are unclear, call search_code.
5. When you have enough evidence, write your final answer.

Final answer format (always end with this structure):

## Root Cause
<one or two sentences stating exactly what went wrong and why>

## Evidence
<bullet list of the specific lines / values that confirm the cause>

## Suggested Fix
<concrete code change or action the developer should take>

## Confidence
<High | Medium | Low> — <one-line justification>
"""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self._file_tools = FileTools()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        trace: str,
        src_dir: Optional[str] = None,
        verbose: bool = False,
    ) -> str:
        """
        Perform root cause analysis on *trace*.

        Args:
            trace: The full stack trace text.
            src_dir: Optional path to the source directory; Claude will use it
                     when calling search_code.
            verbose: When True, print each tool call/result to stdout.

        Returns:
            Claude's final analysis as a markdown string.
        """
        user_content = self._build_user_message(trace, src_dir)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_content}
        ]

        for iteration in range(self.MAX_LOOP_ITERATIONS):
            response = self._client.messages.create(
                model=self.MODEL,
                max_tokens=self.MAX_TOKENS,
                system=self.SYSTEM_PROMPT,
                tools=_TOOL_DEFINITIONS,
                messages=messages,
            )

            # Append the assistant turn to history.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # Extract the final text block.
                return self._extract_text(response.content)

            if response.stop_reason != "tool_use":
                # Unexpected stop — return whatever text we have.
                return self._extract_text(response.content) or (
                    f"[Analysis stopped unexpectedly: stop_reason={response.stop_reason}]"
                )

            # Execute every requested tool call.
            tool_results = self._execute_tool_calls(
                response.content, src_dir=src_dir, verbose=verbose
            )
            messages.append({"role": "user", "content": tool_results})

            if verbose:
                print(f"[debug-agent] Iteration {iteration + 1}: {len(tool_results)} tool result(s)")

        return "[Analysis incomplete: reached maximum iteration limit]"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_user_message(self, trace: str, src_dir: Optional[str]) -> str:
        parts = ["Please analyse the following stack trace and identify the root cause."]
        if src_dir:
            parts.append(f"Source directory: `{src_dir}`")
        parts.append("")
        parts.append("```")
        parts.append(trace.strip())
        parts.append("```")
        return "\n".join(parts)

    def _execute_tool_calls(
        self,
        content: list[Any],
        src_dir: Optional[str],
        verbose: bool,
    ) -> list[dict[str, Any]]:
        """Run every tool_use block in *content* and return tool_result blocks."""
        results: list[dict[str, Any]] = []
        for block in content:
            if block.type != "tool_use":
                continue
            tool_input: dict[str, Any] = block.input
            if verbose:
                print(f"[debug-agent] Tool call: {block.name}({json.dumps(tool_input, indent=2)})")

            output = self._dispatch_tool(block.name, tool_input, src_dir)

            if verbose:
                preview = output[:300].replace("\n", " ") if output else "(empty)"
                print(f"[debug-agent] Tool result preview: {preview}")

            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
            )
        return results

    def _dispatch_tool(
        self, name: str, inputs: dict[str, Any], src_dir: Optional[str]
    ) -> str:
        """Dispatch a tool call to the appropriate handler."""
        if name == "read_file":
            return self._file_tools.read_file(
                path=inputs["path"],
                start_line=inputs.get("start_line"),
                end_line=inputs.get("end_line"),
            )

        if name == "search_code":
            directory = inputs.get("directory") or src_dir or "."
            return self._file_tools.search_code(
                pattern=inputs["pattern"],
                directory=directory,
            )

        if name == "fetch_error_docs":
            return self._fetch_error_docs(inputs["error_type"])

        return f"[error] Unknown tool: {name}"

    @staticmethod
    def _fetch_error_docs(error_type: str) -> str:
        """Return built-in documentation for *error_type*."""
        # Try exact match first, then case-insensitive.
        doc = _ERROR_DOCS.get(error_type)
        if doc:
            return doc
        lower = error_type.lower()
        for key, value in _ERROR_DOCS.items():
            if key.lower() == lower:
                return value
        return (
            f"No built-in documentation for '{error_type}'. "
            "Check the official language/runtime docs for this error class. "
            "Common pattern: read the error message text carefully and "
            "inspect the line numbers in the stack trace."
        )

    @staticmethod
    def _extract_text(content: list[Any]) -> str:
        """Concatenate all TextBlock values from *content*."""
        parts = [block.text for block in content if block.type == "text"]
        return "\n".join(parts).strip()
