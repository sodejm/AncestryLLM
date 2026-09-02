/** Keeps native-verification launch capabilities absent from production builds. */
type CommandLineSwitchReader = Readonly<{
  hasSwitch: (name: string) => boolean
  getSwitchValue: (name: string) => string
}>

/** Production launchers cannot opt into an alternate Linux keyring root. */
export function requestedLinuxKeyringVerificationRoot(
  commandLine: CommandLineSwitchReader,
): undefined {
  void commandLine
  return undefined
}

/** Production launchers cannot opt into the verifier's ephemeral macOS workspace. */
export function requestedMacosEphemeralVerification(
  commandLine: CommandLineSwitchReader,
): false {
  void commandLine
  return false
}
