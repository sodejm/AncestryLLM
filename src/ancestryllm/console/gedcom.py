"""GEDCOM console adapter."""

import cmd2

from ancestryllm.console.command_sets import DelegatingModule


class GedcomModule(DelegatingModule):
    command_name = "gedcom"

    def do_gedcom(self, statement: cmd2.Statement) -> None:
        """Run a GEDCOM action; use `gedcom --help` for syntax."""
        self.dispatch(statement)
