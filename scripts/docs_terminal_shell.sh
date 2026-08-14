#!/bin/bash
# Adapts VHS's supported Bash shell to the repository capture contract.
# The capture runner invokes this script inside the isolated container; it
# accepts VHS-supplied Bash arguments, writes no persistent state itself, and
# preserves the wrapped shell's exit status. It handles no credentials and
# relies on the runner's fixed environment, network denial, and temporary HOME.
set -euo pipefail

cd /workspace
unset PROMPT_COMMAND
stty cols 120 rows 36
exec /bin/bash "$@"
