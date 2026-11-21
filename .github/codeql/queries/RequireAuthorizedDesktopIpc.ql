/**
 * @name Desktop IPC work requires successful sender authorization
 * @description Privileged IPC work must be dominated by successful authorization of the same event.
 * @kind problem
 * @problem.severity error
 * @security-severity 8.1
 * @precision high
 * @id ancestryllm/require-authorized-desktop-ipc
 * @tags security
 *       external/cwe/cwe-862
 */

import javascript
import DesktopGuards

private predicate ipcHandler(Function handler) {
  exists(DataFlow::MethodCallNode registration |
    registration.getMethodName() = "handle" and
    registration.getReceiver().asExpr().(VarRef).getName() = ["ipc", "ipcMain"] and
    handler = registration.getCallback(1).getFunction()
  )
}

private predicate privileged(CallExpr operation) {
  calleeName(operation) =
    [
      "schedule", "capabilityRequest", "requestOpenFileGrant", "requestSaveFileGrant",
      "revokeFileGrant", "readFile", "readFileSync", "writeFile", "writeFileSync", "openExternal",
      "openExternalLink", "openPath", "writeText", "copyText", "openDiagnosticsDirectory",
      "clearDiagnostics"
    ]
}

private predicate authorized(Function handler, CallExpr operation) {
  exists(CallExpr check, DataFlow::FunctionNode fn |
    fn.getFunction() = handler and
    check.getEnclosingFunction() = handler and
    calleeName(check) = "authorize" and
    definiteValue(fn.getParameter(0), check.getArgument(0).flow()) and
    successfulGuard(check.flow().(DataFlow::SourceNode), operation)
  )
}

from Function handler, CallExpr operation
where
  ipcHandler(handler) and
  desktopMain(operation) and
  operation.getEnclosingFunction() = handler and
  privileged(operation) and
  not authorized(handler, operation)
select operation, "Authorize this IPC event and require a successful result before privileged work."
