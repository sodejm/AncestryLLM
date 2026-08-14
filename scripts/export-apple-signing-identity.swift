/// Exports an Apple signing identity for the isolated macOS release keychain.
import Foundation
import Security

/// Writes a stable release-helper error to standard error and terminates with status 1.
///
/// - Parameter message: Sanitized operational context that must not contain credentials.
private func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("ERROR: \(message)\n".utf8))
    exit(1)
}

private var arguments = Array(CommandLine.arguments.dropFirst())
private var identityName: String?
private var outputPath: String?

while !arguments.isEmpty {
    let argument = arguments.removeFirst()
    switch argument {
    case "--identity-name":
        guard !arguments.isEmpty else {
            fail("--identity-name requires a value")
        }
        identityName = arguments.removeFirst()
    case "--output":
        guard !arguments.isEmpty else {
            fail("--output requires a value")
        }
        outputPath = arguments.removeFirst()
    default:
        fail("unknown argument")
    }
}

guard let requiredIdentityName = identityName, !requiredIdentityName.isEmpty else {
    fail("a non-empty --identity-name is required")
}
guard let requiredOutputPath = outputPath, !requiredOutputPath.isEmpty else {
    fail("a non-empty --output is required")
}

guard var password = String(
    data: FileHandle.standardInput.readDataToEndOfFile(),
    encoding: .utf8
) else {
    fail("the PKCS#12 password was not valid UTF-8")
}
password = password.trimmingCharacters(in: .newlines)
guard !password.isEmpty else {
    fail("a non-empty PKCS#12 password is required on standard input")
}

let query: [CFString: Any] = [
    kSecClass: kSecClassIdentity,
    kSecMatchLimit: kSecMatchLimitAll,
    kSecReturnRef: true,
]
var queryResult: CFTypeRef?
let queryStatus = SecItemCopyMatching(query as CFDictionary, &queryResult)
guard queryStatus == errSecSuccess else {
    fail("could not read signing identities from the macOS keychain (status \(queryStatus))")
}

guard let identities = queryResult as? [SecIdentity] else {
    fail("the macOS keychain returned an unexpected identity result")
}

var matchingIdentities: [SecIdentity] = []
for identity in identities {
    var certificate: SecCertificate?
    let copyStatus = SecIdentityCopyCertificate(identity, &certificate)
    guard copyStatus == errSecSuccess, let certificate else {
        continue
    }

    var commonName: CFString?
    let nameStatus = SecCertificateCopyCommonName(certificate, &commonName)
    if nameStatus == errSecSuccess,
       let candidateName = commonName as String?,
       candidateName == requiredIdentityName {
        matchingIdentities.append(identity)
    }
}

guard matchingIdentities.count == 1, let selectedIdentity = matchingIdentities.first else {
    fail("expected exactly one keychain identity matching the selected Developer ID Application certificate")
}

var exportParameters = SecItemImportExportKeyParameters()
exportParameters.version = UInt32(SEC_KEY_IMPORT_EXPORT_PARAMS_VERSION)
exportParameters.passphrase = Unmanaged.passUnretained(password as CFString)

var exportedData: CFData?
let exportStatus = SecItemExport(
    selectedIdentity,
    .formatPKCS12,
    [],
    &exportParameters,
    &exportedData
)
guard exportStatus == errSecSuccess, let exportedData else {
    fail("could not export the selected keychain identity as PKCS#12 (status \(exportStatus))")
}

do {
    try (exportedData as Data).write(
        to: URL(fileURLWithPath: requiredOutputPath),
        options: .atomic
    )
} catch {
    fail("could not write the PKCS#12 output file")
}

password = ""
