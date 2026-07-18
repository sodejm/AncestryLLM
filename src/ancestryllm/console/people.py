"""Research-person console adapter."""

import cmd2

from ancestryllm.console.command_sets import DelegatingModule


class PeopleModule(DelegatingModule):
    command_name = "people"

    def do_people(self, statement: cmd2.Statement) -> None:
        """Manage research people; use `people --help` for syntax."""
        self.dispatch(statement)
