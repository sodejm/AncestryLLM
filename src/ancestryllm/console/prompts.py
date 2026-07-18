"""Saved-prompt console adapter."""

import cmd2

from ancestryllm.console.command_sets import DelegatingModule


class PromptsModule(DelegatingModule):
    command_name = "prompts"

    def do_prompts(self, statement: cmd2.Statement) -> None:
        """Manage prompts; use `prompts --help` for syntax."""
        self.dispatch(statement)
