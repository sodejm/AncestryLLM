"""Secret-reference console adapter."""

import cmd2

from ancestryllm.console.command_sets import DelegatingModule


class SecretsModule(DelegatingModule):
    command_name = "secrets"

    def do_secrets(self, statement: cmd2.Statement) -> None:
        """Manage secret references; values are entered without echo."""
        self.dispatch(statement)
