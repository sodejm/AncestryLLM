"""Shared thin delegation base for built-in console modules."""

from __future__ import annotations

import shlex

import cmd2

from ancestryllm.cli import run_tokens
from ancestryllm.core.context import AppContext
from ancestryllm.core.modules import ModuleDescriptor


class DelegatingModule(cmd2.CommandSet[cmd2.Cmd]):
    command_name = ""

    def __init__(self, context: AppContext, descriptor: ModuleDescriptor) -> None:
        super().__init__()
        self.context = context
        self.descriptor = descriptor

    def dispatch(self, statement: cmd2.Statement) -> None:
        try:
            run_tokens(self.context, [self.command_name, *shlex.split(str(statement))])
        except SystemExit:
            return
