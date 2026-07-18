"""Provider-profile console adapter."""

import cmd2

from ancestryllm.console.command_sets import DelegatingModule


class ProvidersModule(DelegatingModule):
    command_name = "providers"

    def do_providers(self, statement: cmd2.Statement) -> None:
        """Manage providers; use `providers --help` for syntax."""
        self.dispatch(statement)
