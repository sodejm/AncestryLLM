/** Enables the unpublished native-verification package's Linux keyring boundary. */
import { LINUX_KEYRING_VERIFICATION_SWITCH } from '../src/main/sidecar-supervisor'

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
