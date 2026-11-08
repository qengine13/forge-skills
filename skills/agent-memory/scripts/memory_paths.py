"""Resolve the public server's isolated data directory."""
import os
from pathlib import Path


def memory_path(name=""):
    return str(Path(os.environ.get("FORGE_MEMORY_HOME", "~/.forge-agent-memory")).expanduser().resolve() / name)
