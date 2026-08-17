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
  it('uses authenticated graceful shutdown when the sidecar exit is observed', async () => {
    const child = new FakeChild()
    const terminateProcess = vi.fn().mockResolvedValue(undefined)
    const removeWorkingDirectory = vi.fn().mockResolvedValue(undefined)
    const sidecar = new NativeRunningSidecar(
      child as never,
      'C:\\Users\\runner\\AppData\\Local\\Temp\\ancestryllm-sidecar-sensitive',
      terminateProcess,
      removeWorkingDirectory,
      'win32',
      10,
    )
    child.stdout.emit(
      'data',
      Buffer.from('{"contract":"test","port":4242,"sidecar_build":"test"}\n'),
    )
    await sidecar.ready
    const requestGracefulShutdown = vi.fn(async () => {
      child.exitCode = 0
      child.emit('exit', 0, null)
    })

    await expect(sidecar.terminate(requestGracefulShutdown)).resolves.toBeUndefined()

    expect(requestGracefulShutdown).toHaveBeenCalledOnce()
    expect(terminateProcess).not.toHaveBeenCalled()
    expect(removeWorkingDirectory).toHaveBeenCalledOnce()
  })

  it('exits cleanly within the graceful-shutdown budget on win32 without process-group cleanup', async () => {
    vi.useFakeTimers()
    try {
      const child = new FakeChild()
      const terminateProcess = vi.fn().mockResolvedValue(undefined)
      const removeWorkingDirectory = vi.fn().mockResolvedValue(undefined)
      const sidecar = new NativeRunningSidecar(
        child as never,
        'C:\\Users\\runner\\AppData\\Local\\Temp\\ancestryllm-sidecar-sensitive',
        terminateProcess,
        removeWorkingDirectory,
        'win32',
      )
      child.stdout.emit(
        'data',
        Buffer.from('{"contract":"test","port":4242,"sidecar_build":"test"}\n'),
      )
      await sidecar.ready
      const requestGracefulShutdown = vi.fn(async () => {
        setTimeout(() => {
          child.exitCode = 0
          child.emit('exit', 0, null)
        }, 5_000)
      })

      const termination = sidecar.terminate(requestGracefulShutdown)
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(5_000)
      await expect(termination).resolves.toBeUndefined()

      expect(requestGracefulShutdown).toHaveBeenCalledOnce()
      expect(terminateProcess).not.toHaveBeenCalled()
      expect(removeWorkingDirectory).toHaveBeenCalledOnce()
    } finally {
      vi.useRealTimers()
    }
  })

  it('falls back to bounded process-tree termination after graceful shutdown fails', async () => {
    vi.useFakeTimers()
    try {
      const child = new FakeChild()
      const terminateProcess = vi.fn().mockResolvedValue(undefined)
      const removeWorkingDirectory = vi.fn().mockResolvedValue(undefined)
      const sidecar = new NativeRunningSidecar(
        child as never,
        '/private/ancestryllm-sidecar-sensitive',
        terminateProcess,
        removeWorkingDirectory,
        'linux',
      )
      child.stdout.emit(
        'data',
        Buffer.from('{"contract":"test","port":4242,"sidecar_build":"test"}\n'),
      )
      await sidecar.ready
      const requestGracefulShutdown = vi.fn().mockRejectedValue(
        new Error('/private/graceful-shutdown-failure'),
      )

      const termination = sidecar.terminate(requestGracefulShutdown)
      await vi.advanceTimersByTimeAsync(0)
      const skippedLeaderExitWait = terminateProcess.mock.calls.length === 1
      await vi.runAllTimersAsync()
      await expect(termination).resolves.toBeUndefined()

      expect(requestGracefulShutdown).toHaveBeenCalledOnce()
      expect(skippedLeaderExitWait).toBe(true)
      expect(terminateProcess).toHaveBeenCalledOnce()
      expect(removeWorkingDirectory).toHaveBeenCalledOnce()
    } finally {
      vi.useRealTimers()
    }
  })

  it('verifies the POSIX process group after a graceful leader exit', async () => {
    const child = new FakeChild()
    const terminateProcess = vi.fn().mockResolvedValue(undefined)
    const removeWorkingDirectory = vi.fn().mockResolvedValue(undefined)
    const sidecar = new NativeRunningSidecar(
      child as never,
      '/private/ancestryllm-sidecar-sensitive',
      terminateProcess,
      removeWorkingDirectory,
      'linux',
      10,
    )
    child.stdout.emit(
      'data',
      Buffer.from('{"contract":"test","port":4242,"sidecar_build":"test"}\n'),
    )
    await sidecar.ready
    const requestGracefulShutdown = vi.fn(async () => {
      child.exitCode = 0
      child.emit('exit', 0, null)
    })

    await expect(sidecar.terminate(requestGracefulShutdown)).resolves.toBeUndefined()

    expect(requestGracefulShutdown).toHaveBeenCalledOnce()
    expect(terminateProcess).toHaveBeenCalledOnce()
    expect(removeWorkingDirectory).toHaveBeenCalledOnce()
  })

  it('falls back when graceful shutdown succeeds without an observed exit', async () => {
    vi.useFakeTimers()
    try {
      const child = new FakeChild()
      const terminateProcess = vi.fn().mockResolvedValue(undefined)
      const removeWorkingDirectory = vi.fn().mockResolvedValue(undefined)
      const sidecar = new NativeRunningSidecar(
        child as never,
        '/private/ancestryllm-sidecar-sensitive',
        terminateProcess,
        removeWorkingDirectory,
        'linux',
      )
      child.stdout.emit(
        'data',
        Buffer.from('{"contract":"test","port":4242,"sidecar_build":"test"}\n'),
      )
      await sidecar.ready
      const requestGracefulShutdown = vi.fn().mockResolvedValue(undefined)

      const termination = sidecar.terminate(requestGracefulShutdown)
      await vi.advanceTimersByTimeAsync(10_000)
      await expect(termination).resolves.toBeUndefined()

      expect(requestGracefulShutdown).toHaveBeenCalledOnce()
      expect(terminateProcess).toHaveBeenCalledWith(child, 8_500)
      expect(removeWorkingDirectory).toHaveBeenCalledOnce()
    } finally {
      vi.useRealTimers()
    }
  })

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

  it('retries transient working-directory cleanup failures on Windows', async () => {
    const child = new FakeChild()
    const terminateProcess = vi.fn().mockResolvedValue(undefined)
    const removeWorkingDirectory = vi.fn().mockResolvedValue(undefined)
    const sidecar = new NativeRunningSidecar(
      child as never,
      'C:\\Users\\runner\\AppData\\Local\\Temp\\ancestryllm-sidecar-sensitive',
      terminateProcess,
      removeWorkingDirectory,
      'win32',
    )

    await expect(sidecar.terminate()).resolves.toBeUndefined()

    expect(removeWorkingDirectory).toHaveBeenCalledWith(
      'C:\\Users\\runner\\AppData\\Local\\Temp\\ancestryllm-sidecar-sensitive',
      {
        recursive: true,
        force: true,
        maxRetries: 5,
        retryDelay: 100,
      },
    )
  })

  it('does not apply Windows cleanup retries on POSIX', async () => {
    const child = new FakeChild()
    const terminateProcess = vi.fn().mockResolvedValue(undefined)
    const removeWorkingDirectory = vi.fn().mockResolvedValue(undefined)
    const sidecar = new NativeRunningSidecar(
      child as never,
      '/private/ancestryllm-sidecar-sensitive',
      terminateProcess,
      removeWorkingDirectory,
      'linux',
    )

    await expect(sidecar.terminate()).resolves.toBeUndefined()

    expect(removeWorkingDirectory).toHaveBeenCalledWith(
      '/private/ancestryllm-sidecar-sensitive',
      { recursive: true, force: true },
    )
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

  it('allows bounded Windows leader-exit observation after taskkill returns', async () => {
    vi.useFakeTimers()
    try {
      const child = new FakeChild()
      const terminateTree = vi.fn(async () => {
        setTimeout(() => {
          child.signalCode = 'SIGKILL'
          child.emit('exit', null, 'SIGKILL')
        }, 1_100)
      })

      const termination = terminateNativeSidecarProcess(
        child as never,
        'win32',
        terminateTree,
      )
      const expectation = expect(termination).resolves.toBeUndefined()
      await Promise.resolve()
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(1_100)
      await expectation

      expect(terminateTree).toHaveBeenCalledWith(4242)
      expect(child.kill).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('accepts a taskkill race only after Windows leader exit is confirmed', async () => {
    vi.useFakeTimers()
    try {
      const child = new FakeChild()
      const terminateTree = vi.fn(async () => {
        setTimeout(() => {
          child.exitCode = 0
          child.emit('exit', 0, null)
        }, 100)
        throw new Error('The process exited before taskkill completed.')
      })

      const termination = terminateNativeSidecarProcess(
        child as never,
        'win32',
        terminateTree,
      )
      let settled = false
      void termination.finally(() => { settled = true })
      const expectation = expect(termination).resolves.toBeUndefined()
      await Promise.resolve()
      await Promise.resolve()
      expect(settled).toBe(false)
      await vi.advanceTimersByTimeAsync(100)
      await expectation

      expect(settled).toBe(true)
      expect(terminateTree).toHaveBeenCalledWith(4242)
      expect(child.kill).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('fails closed when taskkill errors and the Windows leader stays live', async () => {
    const child = new FakeChild()
    const terminateTree = vi.fn(async () => {
      throw new Error('Access denied while terminating the process tree.')
    })

    await expect(terminateNativeSidecarProcess(
      child as never,
      'win32',
      terminateTree,
      undefined,
      0,
      0,
    )).rejects.toThrow('The sidecar process group could not be terminated.')

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
      _options: { windowsHide: boolean; timeout: number },
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
    expect(options).toEqual({ windowsHide: true, timeout: 4_000 })
    expect(typeof callback).toBe('function')
  })

  it('fails boundedly when the taskkill executor never settles', async () => {
    vi.useFakeTimers()
    try {
      const execute = vi.fn(() => undefined)
      const outcome = Promise.race([
        terminateWindowsProcessTree(4242, execute).then(
          () => 'resolved',
          (error: unknown) => error instanceof Error ? error.message : 'rejected',
        ),
        new Promise<string>((resolve) => {
          setTimeout(() => resolve('still-pending'), 4_001)
        }),
      ])

      await vi.advanceTimersByTimeAsync(4_001)

      await expect(outcome).resolves.toBe(
        'The Windows process-tree terminator timed out.',
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('ignores a taskkill callback that arrives after the independent deadline', async () => {
    vi.useFakeTimers()
    try {
      let complete: ((error: Error | null) => void) | undefined
      const execute = vi.fn((
        _file: string,
        _args: string[],
        _options: { windowsHide: boolean; timeout: number },
        callback: (error: Error | null) => void,
      ) => {
        complete = callback
      })
      const termination = terminateWindowsProcessTree(4242, execute, 5)
      const outcome = termination.then(
        () => 'resolved',
        (error: unknown) => error instanceof Error ? error.message : 'rejected',
      )

      await vi.advanceTimersByTimeAsync(5)
      await expect(outcome).resolves.toBe(
        'The Windows process-tree terminator timed out.',
      )

      complete?.(null)
      await expect(outcome).resolves.toBe(
        'The Windows process-tree terminator timed out.',
      )
    } finally {
      vi.useRealTimers()
    }
  })
})
