/** Emits allowlisted, detail-free lifecycle records for packaged shutdown verification. */
import type { AppShutdownFailure } from './app-shutdown'

/** Exact records that may be retained by the packaged release verifier. */
export const APP_SHUTDOWN_DIAGNOSTICS = Object.freeze({
  requested: 'ANCESTRYLLM_SHUTDOWN_PHASE=REQUESTED',
  jobsPrepared: 'ANCESTRYLLM_SHUTDOWN_PHASE=JOBS_PREPARED',
  sidecarStopped: 'ANCESTRYLLM_SHUTDOWN_PHASE=SIDECAR_STOPPED',
  exitAuthorized: 'ANCESTRYLLM_SHUTDOWN_PHASE=EXIT_AUTHORIZED',
  jobsPreparationFailure: 'ANCESTRYLLM_SHUTDOWN_FAILURE=JOBS_PREPARATION',
  sidecarTerminationFailure: 'ANCESTRYLLM_SHUTDOWN_FAILURE=SIDECAR_TERMINATION',
} as const)

/** One exact, sanitized shutdown record accepted by release verification. */
export type AppShutdownDiagnostic =
  typeof APP_SHUTDOWN_DIAGNOSTICS[keyof typeof APP_SHUTDOWN_DIAGNOSTICS]

const APP_SHUTDOWN_DIAGNOSTIC_SET = new Set<string>(
  Object.values(APP_SHUTDOWN_DIAGNOSTICS),
)

const APP_SHUTDOWN_FAILURE_DIAGNOSTICS: Readonly<
  Record<AppShutdownFailure, AppShutdownDiagnostic>
> = Object.freeze({
  'jobs-preparation': APP_SHUTDOWN_DIAGNOSTICS.jobsPreparationFailure,
  'sidecar-termination': APP_SHUTDOWN_DIAGNOSTICS.sidecarTerminationFailure,
})

/** Maps a typed shutdown failure to its exact sanitized diagnostic record. */
export function appShutdownFailureDiagnostic(
  failure: AppShutdownFailure,
): AppShutdownDiagnostic {
  return APP_SHUTDOWN_FAILURE_DIAGNOSTICS[failure]
}

/** Rejects decorated, partial, or arbitrary process output from release evidence. */
export function isAppShutdownDiagnostic(value: string): value is AppShutdownDiagnostic {
  return APP_SHUTDOWN_DIAGNOSTIC_SET.has(value)
}

/** Writes one exact newline-framed record without error details or environment state. */
export function writeAppShutdownDiagnostic(record: AppShutdownDiagnostic): void {
  process.stderr.write(`${record}\n`)
}
