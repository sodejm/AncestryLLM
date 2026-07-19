# Local LLM benchmarks

`scripts/benchmark_local_llm.py` measures one fictional duplicate-adjudication
request against an Ollama model that is already installed and already running.
It never downloads a model, launches a server, changes Ollama settings, or
stores prompts/responses in its output.

Plan a run without network activity:

```console
python scripts/benchmark_local_llm.py --model existing-model
```

Run one request and save aggregate metrics:

```console
python scripts/benchmark_local_llm.py --model existing-model --execute --output benchmark.json
```

The output contains endpoint, model, selected profile label, wall-clock time,
response byte count, and process peak RSS.  A missing server or model exits
with code 2 and a `skip:` explanation; this is an environmental skip, not a
reason to download or configure anything automatically.
