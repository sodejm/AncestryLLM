// Verifies bounded native-sidecar cleanup, including Windows process-tree termination.
import { EventEmitter } from 'node:events'
import { describe, expect, it, vi } from 'vitest'
import {
  terminateNativeSidecarProcess,
  terminateWindowsProcessTree,
} from './sidecar-process'

class FakeChild extends EventEmitter {
  readonly pid = 4242
  exitCode: number | null = null
  signalCode: NodeJS.Signals | null = null
  readonly kill = vi.fn((signal?: NodeJS.Signals | number) => {
    this.signalCode = typeof signal === 'string' ? signal : 'SIGTERM'
    this.emit('exit', null, this.signalCode)
    return true
  })
}

describe('native sidecar process termination', () => {
  it('terminates the complete Windows process tree before cleaning resources', async () => {
    const child = new FakeChild()
    const terminateTree = vi.fn(async () => {
      child.signalCode = 'SIGKILL'
      child.emit('exit', null, 'SIGKILL')
    })

    await terminateNativeSidecarProcess(
      child as never,
      'win32',
      terminateTree,
    )

    expect(terminateTree).toHaveBeenCalledOnce()
    expect(terminateTree).toHaveBeenCalledWith(4242)
    expect(child.kill).not.toHaveBeenCalled()
  })

  it('invokes taskkill with an exact no-shell tree-kill argument vector', async () => {
    const execute = vi.fn((
      _file: string,
      _args: string[],
      _options: { windowsHide: boolean },
      callback: (error: Error | null) => void,
    ) => callback(null))

    await terminateWindowsProcessTree(4242, execute)

    expect(execute).toHaveBeenCalledOnce()
    const call = execute.mock.calls[0]
    expect(call).toBeDefined()
    if (!call) throw new Error('Expected the Windows tree-kill executor call.')
    const [file, args, options, callback] = call
    expect(file).toBe('taskkill.exe')
    expect(args).toEqual(['/PID', '4242', '/T', '/F'])
    expect(options).toEqual({ windowsHide: true })
    expect(typeof callback).toBe('function')
  })
})
