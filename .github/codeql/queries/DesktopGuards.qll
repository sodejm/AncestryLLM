/** Repository-specific desktop boundary vocabulary and control-flow guards. */

import javascript

/** A value must have exactly this local source, excluding conditional replacements. */
predicate definiteValue(DataFlow::SourceNode source, DataFlow::Node target) {
  source.flowsTo(target) and
  forall(DataFlow::SourceNode alternative | alternative.flowsTo(target) | alternative = source) and
  // Primitive constants are not SourceNodes, but can still replace a guard value.
  not exists(DataFlow::Node constant |
    constant.getASuccessor*() = target and constant.asExpr() instanceof Literal
  )
}

/** Local value identity includes aliases, but not transformations or merged values. */
predicate sameValue(Expr left, Expr right) {
  exists(DataFlow::SourceNode source |
    definiteValue(source, left.flow()) and definiteValue(source, right.flow())
  )
}

private predicate resultReaches(DataFlow::SourceNode source, Expr test) {
  definiteValue(source, test.flow())
  or
  exists(AwaitExpr awaited | awaited = test and definiteValue(source, awaited.getOperand().flow()))
}

/** Require the successful outcome, not merely evaluation of a check. */
predicate successfulGuard(DataFlow::SourceNode source, CallExpr operation) {
  exists(ConditionGuardNode guard |
    guard.dominates(operation.getBasicBlock()) and
    (
      guard.getOutcome() = true and resultReaches(source, guard.getTest())
      or
      guard.getOutcome() = false and
      resultReaches(source, guard.getTest().(LogNotExpr).getOperand())
    )
  )
}

string calleeName(CallExpr call) {
  result = call.getCallee().(VarRef).getName()
  or
  result = call.getCallee().(PropAccess).getPropertyName()
}

/** The analyzed production surface; fixture files use the same directory shape. */
predicate desktopMain(CallExpr call) {
  call.getFile().getRelativePath().matches("%desktop/src/main/%")
}
