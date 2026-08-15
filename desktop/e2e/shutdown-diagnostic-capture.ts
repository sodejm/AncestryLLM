/** Retains only exact, bounded shutdown lifecycle records from packaged output. */
import {
  isAppShutdownDiagnostic,
  type AppShutdownDiagnostic,
} from '../src/main/shutdown-diagnostics'

type ShutdownDiagnosticStream = 'stdout' | 'stderr'

const MAX_PENDING_LINE_CHARACTERS = 256
const MAX_CAPTURED_RECORDS = 16

/** Strictly allowlists packaged shutdown records while discarding all other output. */
export class ShutdownDiagnosticCapture {
  private readonly pending: Record<ShutdownDiagnosticStream, string> = {
    stdout: '',
    stderr: '',
  }
  private readonly discardingLongLine: Record<ShutdownDiagnosticStream, boolean> = {
    stdout: false,
    stderr: false,
  }
  private readonly records: AppShutdownDiagnostic[] = []

  /** Consumes one process-output chunk without retaining arbitrary complete lines. */
  consume(stream: ShutdownDiagnosticStream, chunk: Buffer | string): void {
    const portions = chunk.toString().split('\n')
    for (const [index, portion] of portions.entries()) {
      const completesLine = index < portions.length - 1
      if (this.discardingLongLine[stream]) {
        if (completesLine) this.discardingLongLine[stream] = false
        continue
      }

      const candidate = `${this.pending[stream]}${portion}`
      this.pending[stream] = ''
      if (candidate.length > MAX_PENDING_LINE_CHARACTERS) {
        if (!completesLine) this.discardingLongLine[stream] = true
        continue
      }
      if (!completesLine) {
        this.pending[stream] = candidate
        continue
      }

      const line = candidate.endsWith('\r') ? candidate.slice(0, -1) : candidate
      if (!isAppShutdownDiagnostic(line)) continue
      this.records.push(line)
      if (this.records.length > MAX_CAPTURED_RECORDS) this.records.shift()
    }
  }

  /** Returns an immutable copy containing no arbitrary process output. */
  snapshot(): readonly AppShutdownDiagnostic[] {
    return [...this.records]
  }

  /** Formats the sanitized timeout context consumed by release workflow logs. */
  timeoutContext(): string {
    return `shutdown_diagnostics=${JSON.stringify(this.records)}`
  }
}
