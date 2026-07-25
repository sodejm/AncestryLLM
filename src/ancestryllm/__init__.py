"""AncestryLLM modular genealogy research platform."""

from importlib.metadata import PackageNotFoundError, version

from ancestryllm.core.errors import AncestryError

try:
    __version__ = version("ancestryllm")
except PackageNotFoundError:  # pragma: no cover - only an uninstalled source tree
    __version__ = "unknown"

__all__ = ["AncestryError", "__version__"]
