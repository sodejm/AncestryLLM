"""MSFConsole-style local shell with dangerous built-ins disabled."""

from __future__ import annotations

import shlex
from pathlib import Path

import cmd2

from ancestryllm.cli import run_tokens
from ancestryllm.core.context import AppContext
from ancestryllm.core.modules import BUILTIN_MODULES, ModuleRegistry


class AncestryConsole(cmd2.Cmd):
    intro = "AncestryLLM local research console. Type `modules` or `help`."

    def __init__(self, context: AppContext) -> None:
        history = context.config.data_dir / "console_history"
        super().__init__(
            allow_cli_args=False,
            allow_redirection=False,
            auto_load_commands=False,
            include_ipy=False,
            persistent_history_file=str(history),
        )
        self.context = context
        self.registry = ModuleRegistry(context)
        self.active_module: str | None = None
        self.module_options: dict[str, str] = {}
        self.prompt = "ancestry > "
        for module in self.registry.load():
            self.register_command_set(module)
        for unsafe in ("shell", "py", "run_pyscript", "run_script", "edit", "shortcuts"):
            try:
                self.disable_command(unsafe, "Disabled by AncestryLLM security policy.")
            except (AttributeError, cmd2.CommandSetRegistrationError):
                pass
        try:
            Path(history).touch(mode=0o600, exist_ok=True)
            Path(history).chmod(0o600)
        except OSError:
            pass

    def do_modules(self, _statement: cmd2.Statement) -> None:
        """List enabled built-in modules."""
        for descriptor in self.registry.descriptors():
            self.poutput(f"{descriptor.module_id:12} {descriptor.summary}")

    def do_use(self, statement: cmd2.Statement) -> None:
        """Enter a module context: use MODULE."""
        module_id = str(statement).strip()
        if module_id not in self.context.config.enabled_modules or module_id not in BUILTIN_MODULES:
            self.perror(f"Module is not enabled: {module_id}")
            return
        self.active_module = module_id
        self.module_options.clear()
        self.prompt = f"ancestry({module_id}) > "

    def do_info(self, _statement: cmd2.Statement) -> None:
        """Show active module metadata."""
        if not self.active_module:
            self.perror("Use a module first.")
            return
        descriptor = BUILTIN_MODULES[self.active_module]
        self.poutput(f"{descriptor.name}: {descriptor.summary}")
        self.poutput("Actions: " + ", ".join(descriptor.actions))

    def do_show(self, statement: cmd2.Statement) -> None:
        """Show active module actions or options."""
        target = str(statement).strip() or "options"
        if not self.active_module:
            self.perror("Use a module first.")
            return
        if target == "actions":
            self.poutput("\n".join(BUILTIN_MODULES[self.active_module].actions))
        elif target == "options":
            for name, value in sorted(self.module_options.items()):
                self.poutput(f"{name} = {value}")
        else:
            self.perror("Use `show actions` or `show options`.")

    def do_set(self, statement: cmd2.Statement | str) -> None:
        """Set an active-module option: set NAME VALUE."""
        parts = shlex.split(str(statement))
        if len(parts) < 2:
            self.perror("Usage: set NAME VALUE")
            return
        name = parts[0].replace("-", "_")
        if any(word in name.casefold() for word in ("secret", "password", "api_key", "token")):
            self.perror(
                "Secrets must be entered through `secrets set` and cannot be module options."
            )
            return
        self.module_options[name] = " ".join(parts[1:])

    def do_unset(self, statement: cmd2.Statement) -> None:
        """Unset an active-module option."""
        self.module_options.pop(str(statement).strip().replace("-", "_"), None)

    def do_run(self, statement: cmd2.Statement) -> None:
        """Run the selected module action using saved options."""
        if not self.active_module:
            self.perror("Use a module first.")
            return
        supplied = shlex.split(str(statement))
        action = supplied[0] if supplied else self.module_options.get("action")
        if not action:
            self.perror("Set `action` or pass an action to `run`.")
            return
        tokens = [self.active_module, action]
        for name, value in sorted(self.module_options.items()):
            if name == "action":
                continue
            tokens.extend(["--" + name.replace("_", "-"), value])
        tokens.extend(supplied[1:] if supplied else [])
        try:
            run_tokens(self.context, tokens)
        except SystemExit:
            return

    def do_back(self, _statement: cmd2.Statement) -> None:
        """Leave the active module context."""
        self.active_module = None
        self.module_options.clear()
        self.prompt = "ancestry > "
