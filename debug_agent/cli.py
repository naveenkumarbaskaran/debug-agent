"""CLI entry point for debug-agent."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from .agent import DebugAgent

console = Console()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """debug-agent — AI-powered root cause analysis for stack traces.

    \b
    Quick start:
      debug-agent analyze --trace error.txt --src ./src
      cat error.txt | debug-agent analyze
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
@click.option(
    "--trace",
    "-t",
    type=click.Path(exists=True, readable=True, path_type=Path),
    default=None,
    help="Path to a file containing the stack trace. "
         "If omitted the trace is read from stdin.",
)
@click.option(
    "--src",
    "-s",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Source directory to search when the agent needs to read files. "
         "If omitted the agent can still read files using absolute paths "
         "embedded in the trace.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Print each tool call and result preview to stderr.",
)
@click.option(
    "--api-key",
    envvar="ANTHROPIC_API_KEY",
    default=None,
    help="Anthropic API key. Defaults to $ANTHROPIC_API_KEY.",
)
def analyze(
    trace: Path | None,
    src: Path | None,
    verbose: bool,
    api_key: str | None,
) -> None:
    """Analyse a stack trace and print a root cause report.

    \b
    Examples:
      # from a file
      debug-agent analyze --trace crash.txt --src ./myapp

      # pipe mode
      python myapp.py 2>&1 | debug-agent analyze --src ./myapp

      # verbose (see every tool call)
      debug-agent analyze -t crash.txt -s ./myapp -v
    """
    # Read the trace from file or stdin.
    if trace is not None:
        trace_text = trace.read_text(encoding="utf-8", errors="replace")
    else:
        if sys.stdin.isatty():
            console.print(
                "[yellow]No --trace file given. Paste the stack trace below and press "
                "Ctrl-D (Unix) or Ctrl-Z + Enter (Windows) when done:[/yellow]"
            )
        trace_text = sys.stdin.read()

    trace_text = trace_text.strip()
    if not trace_text:
        console.print("[red]Error: empty stack trace — nothing to analyse.[/red]")
        sys.exit(1)

    src_str = str(src) if src is not None else None

    # Show a header panel.
    _print_header(trace_text, src_str)

    # Run the agent.
    with console.status("[bold cyan]Analysing…[/bold cyan]", spinner="dots"):
        try:
            agent = DebugAgent(api_key=api_key)
            report = agent.analyze(
                trace=trace_text,
                src_dir=src_str,
                verbose=verbose,
            )
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Agent error: {exc}[/red]")
            sys.exit(1)

    # Print the report as rendered Markdown.
    console.print()
    console.rule("[bold green]Root Cause Analysis")
    console.print(Markdown(report))
    console.rule()


def _print_header(trace_text: str, src_dir: str | None) -> None:
    """Print a summary panel with trace stats."""
    lines = trace_text.splitlines()
    # Try to extract the error type from a common Python/Java/Node pattern.
    error_hint = "(unknown)"
    for line in reversed(lines):
        line = line.strip()
        if ": " in line and not line.startswith("File ") and not line.startswith("at "):
            error_hint = line[:120]
            break

    info = Text()
    info.append("Trace lines : ", style="bold")
    info.append(f"{len(lines)}\n")
    info.append("Last error  : ", style="bold")
    info.append(error_hint + "\n", style="red")
    if src_dir:
        info.append("Source dir  : ", style="bold")
        info.append(src_dir)

    console.print(
        Panel(info, title="[bold blue]debug-agent[/bold blue]", expand=False)
    )


def main() -> None:
    """Package entry point."""
    cli()


if __name__ == "__main__":
    main()
