"""OCR console adapter."""

import cmd2

from ancestryllm.console.command_sets import DelegatingModule


class OcrModule(DelegatingModule):
    command_name = "ocr"

    def do_ocr(self, statement: cmd2.Statement) -> None:
        """Run an OCR action; use `ocr --help` for syntax."""
        self.dispatch(statement)
