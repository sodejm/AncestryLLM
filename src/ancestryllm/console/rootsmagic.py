"""RootsMagic console adapter."""

import cmd2

from ancestryllm.console.command_sets import DelegatingModule


class RootsMagicModule(DelegatingModule):
    command_name = "rootsmagic"

    def do_rootsmagic(self, statement: cmd2.Statement) -> None:
        """Run a RootsMagic action; use `rootsmagic --help` for syntax."""
        self.dispatch(statement)
