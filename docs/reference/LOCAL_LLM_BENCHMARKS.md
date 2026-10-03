# Local LLM benchmarks

`scripts/benchmark_local_llm.py` benchmarks an already-installed Ollama model
using a fictional duplicate-adjudication workload. It never downloads models,
starts Ollama, changes persistent settings, or saves prompts/responses. Nothing
contacts Ollama until `--execute` is supplied.

Plan a run without network activity:

```console
python scripts/benchmark_local_llm.py --model existing-model
```

Run a first-request (observed cold) measurement plus one warm measurement:

```console
python scripts/benchmark_local_llm.py --model existing-model --execute \
  --output /tmp/ollama-benchmark.json
```

The JSON report is aggregate-only: selected profile and its request options,
model metadata, resident model memory when Ollama makes it available, benchmark
process peak RSS when the platform exposes it, and per-request wall time, TTFT,
Ollama load/total time, and prompt/completion token rates. It contains no
prompt, generated response, or endpoint value. `--output` refuses paths inside
this repository so reports cannot be accidentally committed.

`cold` means the first observed benchmark request by default; an already-loaded
model may therefore still be warm at the Ollama level. To explicitly request an
unload before that measurement, use `--unload-before`. This makes a local
Ollama API call but does not download or remove the model. `--unload-after`
requests the same unload once measurements finish and records whether it
succeeded.

For operational behavior checks, `--warm-runs N` adds warm requests,
`--queue-depth N` submits an additional concurrent batch and records each
worker's local queue delay, and `--cancel-after-first-token` closes each stream
after its first generated token and records `cancelled`. `--timeout-seconds N`
sets the HTTP timeout; a timed-out request remains in the JSON report with a
`timeout` status. A missing endpoint or unavailable model exits with code 2 and
a clear `skip:` message; the script will not fetch or configure anything.
