"""debug-agent: AI-powered stack trace root cause analysis using Claude."""

from .agent import DebugAgent
from .tools import FileTools

__all__ = ["DebugAgent", "FileTools"]
__version__ = "0.1.0"
