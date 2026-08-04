export const WINDOW_READY_RECORD = '{"event":"ancestryllm.desktop.window-ready","version":1}'

export function outputContainsWindowReadyRecord(output: string): boolean {
  return output.split(/\r?\n/u).includes(WINDOW_READY_RECORD)
}
