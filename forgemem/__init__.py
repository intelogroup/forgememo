"""Forgemem — persistent memory for AI agents."""
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("forgemem")
except PackageNotFoundError:
    # Package not installed (running from source checkout)
    __version__ = "0.0.0+dev"
