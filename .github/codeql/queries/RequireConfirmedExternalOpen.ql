/**
 * @name External opening requires validation and destination-bound confirmation
 * @description External destinations must be validated and affirmatively confirmed before opening.
 * @kind problem
 * @problem.severity error
 * @security-severity 7.5
 * @precision high
 * @id ancestryllm/require-confirmed-external-open
 * @tags security
 *       external/cwe/cwe-862
 */

import javascript
import DesktopGuards

private predicate validatedAndConfirmed(CallExpr operation) {
  exists(CallExpr validation, CallExpr confirmation |
    validation.getEnclosingFunction() = operation.getEnclosingFunction() and
    confirmation.getEnclosingFunction() = operation.getEnclosingFunction() and
    calleeName(validation) = "validateExternalLink" and
    calleeName(confirmation) = "confirm" and
    definiteValue(validation.flow().(DataFlow::SourceNode), operation.getArgument(0).flow()) and
    definiteValue(validation.flow().(DataFlow::SourceNode), confirmation.getArgument(0).flow()) and
    successfulGuard(confirmation.flow().(DataFlow::SourceNode), operation)
  )
}

/** The injected adapter may only forward its normalized argument to Electron. */
private predicate normalizedAdapter(CallExpr operation) {
  exists(
    DataFlow::CallNode wrapper, DataFlow::FunctionNode adapter, DataFlow::FunctionNode confirmation,
    CallExpr prompt, CallExpr dialog, ReturnStmt returned, BinaryExpr affirmative,
    PropAccess response, CallExpr forwarded
  |
    wrapper.getCalleeName() = "openExternalLinkWithConfirmation" and
    wrapper.getACallee() = forwarded.getEnclosingFunction() and
    calleeName(forwarded) = "openExternal" and
    validatedAndConfirmed(forwarded) and
    definiteValue(adapter, wrapper.getOptionArgument(1, "openExternal")) and
    adapter.getFunction() = operation.getEnclosingFunction() and
    definiteValue(adapter.getParameter(0), operation.getArgument(0).flow()) and
    definiteValue(confirmation, wrapper.getOptionArgument(1, "confirm")) and
    prompt.getEnclosingFunction() = confirmation.getFunction() and
    calleeName(prompt) = "externalLinkPrompt" and
    definiteValue(confirmation.getParameter(0), prompt.getArgument(0).flow()) and
    calleeName(dialog) = "showMessageBox" and
    dialog.getArgument(0) = prompt and
    returned.getTarget() = confirmation.getFunction() and
    returned.getExpr() = affirmative and
    affirmative.getOperator() = "===" and
    affirmative.getLeftOperand() = response and
    response.getPropertyName() = "response" and
    affirmative.getRightOperand().(NumberLiteral).getIntValue() = 1 and
    sameValue(response.getBase(), dialog.getParent().(AwaitExpr)) and
    // A second return (for example `return true` on cancellation) is not trusted.
    forall(ReturnStmt other | other.getTarget() = confirmation.getFunction() | other = returned)
  )
}

from CallExpr operation
where
  desktopMain(operation) and
  calleeName(operation) = "openExternal" and
  not validatedAndConfirmed(operation) and
  not normalizedAdapter(operation)
select operation,
  "Validate and affirmatively confirm this same normalized destination before opening it."
