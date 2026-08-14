/** Verifies bounded native-sidecar cleanup, including Windows process-tree termination. */
import { EventEmitter } from 'node:events'
import { describe, expect, it, vi } from 'vitest'
import {
  createPosixProcessGroupController,
  NativeRunningSidecar,
  nativeSidecarSpawnOptions,
  terminateNativeSidecarProcess,
  terminateWindowsProcessTree,
} from './sidecar-process'

class FakeChild extends EventEmitter {
  readonly pid = 4242
  exitCode: number | null = null
  signalCode: NodeJS.Signals | null = null
  readonly stderr = { resume: vi.fn() }
  readonly stdout = new EventEmitter()
  readonly kill = vi.fn((signal?: NodeJS.Signals | number) => {
    this.signalCode = typeof signal === 'string' ? signal : 'SIGTERM'
    this.emit('exit', null, this.signalCode)
    return true
  })
}

describe('native sidecar process termination', () => {
  it('retries native cleanup after a failed termination attempt', async () => {
    const child = new FakeChild()
    const terminateProcess = vi.fn()
      .mockRejectedValueOnce(new Error('/private/process-tree-cleanup-failure'))
      .mockResolvedValueOnce(undefined)
    const removeWorkingDirectory = vi.fn().mockResolvedValue(undefined)
    const sidecar = new NativeRunningSidecar(
      child as never,
      '/private/ancestryllm-sidecar-sensitive',
      terminateProcess,
      removeWorkingDirectory,
    )

    await expect(sidecar.terminate()).rejects.toThrow(
      '/private/process-tree-cleanup-failure',
    )
    await expect(sidecar.terminate()).resolves.toBeUndefined()
    await expect(sidecar.terminate()).resolves.toBeUndefined()

    expect(terminateProcess).toHaveBeenCalledTimes(2)
    expect(removeWorkingDirectory).toHaveBeenCalledOnce()
  })

  it('targets the negative POSIX process-group id', () => {
    const kill = vi.fn(() => true)
    const controller = createPosixProcessGroupController(kill)

    controller.signal(4242, 'SIGTERM')
    expect(controller.exists(4242)).toBe(true)

    expect(kill).toHaveBeenNthCalledWith(1, -4242, 'SIGTERM')
    expect(kill).toHaveBeenNthCalledWith(2, -4242, 0)
  })

  it('creates an isolated POSIX process group without a shell', () => {
    expect(nativeSidecarSpawnOptions('/private/work', {}, 'darwin')).toEqual({
      cwd: '/private/work', env: {}, shell: false,
      stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true, detached: true,
    })
  })

  it('terminates the complete POSIX process group with bounded escalation', async () => {
    const child = new FakeChild()
    child.kill.mockImplementation(() => true)
    const signalGroup = vi.fn()
      .mockImplementationOnce(() => undefined)
      .mockImplementationOnce(() => undefined)
    const groupExists = vi.fn()
      .mockReturnValueOnce(true)
      .mockReturnValueOnce(true)
      .mockReturnValueOnce(false)

    await terminateNativeSidecarProcess(
      child as never,
      'darwin',
      undefined,
      { signal: signalGroup, exists: groupExists },
      0,
    )

    expect(signalGroup).toHaveBeenNthCalledWith(1, 4242, 'SIGTERM')
    expect(signalGroup).toHaveBeenNthCalledWith(2, 4242, 'SIGKILL')
    expect(child.kill).not.toHaveBeenCalled()
  })

  it('terminates surviving POSIX descendants after the group leader exits', async () => {
    const child = new FakeChild()
    child.exitCode = 1
    const signalGroup = vi.fn()
    const groupExists = vi.fn()
      .mockReturnValueOnce(true)
      .mockReturnValueOnce(false)

    await terminateNativeSidecarProcess(
      child as never,
      'linux',
      undefined,
      { signal: signalGroup, exists: groupExists },
      0,
    )

    expect(signalGroup).toHaveBeenCalledWith(4242, 'SIGTERM')
    expect(child.kill).not.toHaveBeenCalled()
  })

  it('fails closed when a POSIX process group survives forced termination', async () => {
    const child = new FakeChild()
    child.kill.mockImplementation(() => true)
    const signalGroup = vi.fn()
    const groupExists = vi.fn(() => true)

    await expect(terminateNativeSidecarProcess(
      child as never,
      'linux',
      undefined,
      { signal: signalGroup, exists: groupExists },
      0,
    )).rejects.toThrow('The sidecar process group did not terminate.')

    expect(signalGroup).toHaveBeenNthCalledWith(1, 4242, 'SIGTERM')
    expect(signalGroup).toHaveBeenNthCalledWith(2, 4242, 'SIGKILL')
    expect(child.kill).not.toHaveBeenCalled()
  })

  it('fails closed when POSIX group cleanup errors after its leader exits', async () => {
    const child = new FakeChild()
    child.exitCode = 1
    const controllerError = Object.assign(new Error('/private/sensitive/path'), {
      code: 'EPERM',
    })
    const groupExists = vi.fn(() => { throw controllerError })

    let cleanupError: unknown
    try {
      await terminateNativeSidecarProcess(
        child as never,
        'darwin',
        undefined,
        { signal: vi.fn(), exists: groupExists },
        0,
      )
    } catch (error) {
      cleanupError = error
    }

    expect(cleanupError).toEqual(
      new Error('The sidecar process group could not be terminated.'),
    )
    expect(child.kill).not.toHaveBeenCalled()
  })

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

  it('fails closed when Windows tree cleanup cannot confirm leader exit', async () => {
    const child = new FakeChild()
    const terminateTree = vi.fn(async () => undefined)

    await expect(terminateNativeSidecarProcess(
      child as never,
      'win32',
      terminateTree,
      undefined,
      0,
    )).rejects.toThrow('The sidecar process group did not terminate.')

    expect(terminateTree).toHaveBeenCalledWith(4242)
    expect(child.kill).not.toHaveBeenCalled()
  })

  it('fails closed when fallback forced termination cannot confirm exit', async () => {
    const child = new FakeChild()
    Object.defineProperty(child, 'pid', { value: undefined })
    child.kill.mockImplementation(() => true)

    await expect(terminateNativeSidecarProcess(
      child as never,
      'win32',
      undefined,
      undefined,
      0,
    )).rejects.toThrow('The sidecar process group did not terminate.')

    expect(child.kill).toHaveBeenNthCalledWith(1, 'SIGTERM')
    expect(child.kill).toHaveBeenNthCalledWith(2, 'SIGKILL')
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
