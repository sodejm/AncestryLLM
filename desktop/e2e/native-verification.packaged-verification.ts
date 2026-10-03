/** Enables the unpublished native-verification package's isolated storage boundaries. */
import {
  LINUX_KEYRING_VERIFICATION_SWITCH,
  MACOS_EPHEMERAL_VERIFICATION_SWITCH,
} from '../src/main/sidecar-supervisor'

type CommandLineSwitchReader = Readonly<{
  hasSwitch: (name: string) => boolean
  getSwitchValue: (name: string) => string
}>

/** Reads the selector compiled only into the dedicated verification package. */
export function requestedLinuxKeyringVerificationRoot(
  commandLine: CommandLineSwitchReader,
): string | undefined {
  if (!commandLine.hasSwitch(LINUX_KEYRING_VERIFICATION_SWITCH)) return undefined
  return commandLine.getSwitchValue(LINUX_KEYRING_VERIFICATION_SWITCH)
}

/** Selects the ephemeral macOS workspace only in the unpublished verifier. */
export function requestedMacosEphemeralVerification(
  commandLine: CommandLineSwitchReader,
): boolean {
  return commandLine.hasSwitch(MACOS_EPHEMERAL_VERIFICATION_SWITCH)
}
