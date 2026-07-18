# Built-in module authoring

A module declares a `ModuleDescriptor` and a `cmd2.CommandSet` in the explicit
registry. Its console adapter delegates to a service; it must not open storage,
read secrets, call providers, or implement business rules directly. Add the same
argument parser action to the one-shot dispatcher and command set so behavior and
errors remain identical.

Modules are built-in only in v1. Add tests proving disabled modules are not
imported, commands are unavailable, service DTOs serialize, secrets stay out of
history, and offline defaults make no network calls.
