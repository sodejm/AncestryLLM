"""Privacy-safe, local observability contracts."""

from ancestryllm.observability.structured_diagnostics import (
    DESKTOP_DIAGNOSTIC_SCHEMA_VERSION,
    DesktopDiagnosticComponent,
    DesktopDiagnosticEvent,
    DesktopDiagnosticSeverity,
    DesktopDiagnosticWriter,
    create_desktop_diagnostic_event,
    create_desktop_diagnostic_run_id,
)

__all__ = [
    "DESKTOP_DIAGNOSTIC_SCHEMA_VERSION",
    "DesktopDiagnosticComponent",
    "DesktopDiagnosticEvent",
    "DesktopDiagnosticSeverity",
    "DesktopDiagnosticWriter",
    "create_desktop_diagnostic_event",
    "create_desktop_diagnostic_run_id",
]
