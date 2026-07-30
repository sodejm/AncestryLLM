"""Shared terminal adapter infrastructure for one-shot and interactive hosts.

Import concrete seams from their owning submodules.  Keeping this package
initializer inert lets parser-only consumers remain independent of Rich and
the rest of the interactive terminal stack.
"""

__all__: list[str] = []
