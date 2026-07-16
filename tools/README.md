# GEDCOM Merge Tool

`gedcom_merge.py` combines two or more GEDCOM files into one loss-minimizing
master tree. It standardizes dates, preserves conflicting facts and custom
tags, resolves cross-file pointers, optionally exports only the connected tree
around a root person, and can use local or remote AI to adjudicate uncertain
duplicate people.

AI is used only for duplicate decisions. A model cannot delete a conflicting
fact: both source fact blocks remain in the merged GEDCOM.

## Quick start

Run these commands from the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

For a completely offline merge with no AI calls:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend none --auto -o master.ged
```

For local AI through Ollama, which is the default and does not need an API key:

```bash
ollama pull llama3.1
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend ollama --auto -o master.ged
```

Use `python tools/gedcom_merge.py --help` for every option.

## Environment and API keys

The script loads `.env` automatically through `python-dotenv`. Put `.env` in
the repository root, next to `requirements.txt`; do not put it in `tools/`.
The repository ignores `.env` and `.env.*`, while retaining only the blank
`.env.example` template.

```bash
cp .env.example .env
chmod 600 .env  # macOS/Linux: optional but recommended
```

Set only the providers you intend to use:

```dotenv
# Local Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1

# Direct OpenAI
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini

# Direct Google Gemini: set GEMINI_API_KEY or GOOGLE_API_KEY, not both
GEMINI_API_KEY=
GOOGLE_API_KEY=
GEMINI_MODEL=gemini-3.5-flash

# OpenRouter and managed model routing
OPENROUTER_API_KEY=
OPENROUTER_MANAGEMENT_KEY=
OPENROUTER_MODEL=openrouter/auto
OPENROUTER_COST_QUALITY=7
OPENROUTER_ZDR=true

# Merge defaults
GEDCOM_AI_BACKEND=ollama
AI_REASONING_EFFORT=low
REMOTE_CREDIT_CHECK=required
MINIMUM_REMOTE_CREDIT_USD=0.01
```

Create keys only on the providers' official pages:

- OpenAI: <https://platform.openai.com/api-keys>
- Google AI Studio: <https://aistudio.google.com/app/apikey>
- OpenRouter API keys: <https://openrouter.ai/settings/keys>
- OpenRouter management keys:
  <https://openrouter.ai/settings/management-keys>

Never pass API keys as command-line arguments, commit `.env`, paste keys into a
GEDCOM, or store them in shell history. The tool reads keys from the environment
and never writes them to output.

## Recommended remote setup: OpenRouter Auto Router

OpenRouter's Auto Router can choose a current model using a server-side
cost/quality policy. By default, this tool restricts it to OpenAI GPT-5 and
Google Gemini model families, denies providers that collect prompt data, and
requires zero-data-retention endpoints. Use `--no-openrouter-zdr` only after a
deliberate privacy review.

For strict account-credit checking, set both keys:

```dotenv
OPENROUTER_API_KEY=your-inference-key
OPENROUTER_MANAGEMENT_KEY=your-management-key
REMOTE_CREDIT_CHECK=required
OPENROUTER_ZDR=true
```

Then run:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend auto \
  --openrouter-zdr \
  --auto \
  -o master.ged
```

The default cost/quality value is `7`; `0` favors quality and `10` favors cost
savings. Override it and the permitted model pool when needed:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend openrouter \
  --openrouter-cost-quality 5 \
  --openrouter-allowed-model 'openai/gpt-5*' \
  --openrouter-allowed-model 'google/gemini-*' \
  --openrouter-zdr --auto -o master.ged
```

The selected provider/model is logged for each AI decision. Model IDs are
configuration, so a model rename or retirement normally requires only an
environment or CLI change rather than a code edit.

## Direct OpenAI or Gemini

Direct APIs are supported with the official `openai` and `google-genai` Python
SDKs. The OpenAI Agents SDK is not required: each adjudication is one bounded,
structured-output request rather than an autonomous multi-agent workflow.

OpenAI:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend openai \
  --openai-model gpt-5.4-mini \
  --reasoning-effort low \
  --credit-check best-effort \
  --auto -o master.ged
```

Gemini:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend gemini \
  --gemini-model gemini-3.5-flash \
  --credit-check best-effort \
  --auto -o master.ged
```

### Why direct providers use `best-effort`

OpenRouter documents an account-credit endpoint for management keys. Normal
OpenAI and Gemini inference keys do not currently have a documented API that
returns remaining prepaid balance. Their dashboards show billing information,
but an authentication/model probe is not proof of available credits.

The default `--credit-check required` therefore blocks direct OpenAI and Gemini
before any person data is sent. `--credit-check best-effort` is an explicit
acknowledgement that the provider may reject the eventual request for quota or
billing reasons. `--credit-check off` is available but is not recommended.

Credit preflights contain credentials and billing metadata only—never names,
dates, relationships, GEDCOM lines, or model prompts. Auto routing may fall
back after a failed preflight, but it does not retry an already-submitted
person prompt through a second remote provider.

## Rooted tree export

Use an existing GEDCOM pointer or a unique full name as the root. The output
contains that person's connected family graph rather than unrelated branches:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --root-person '@I123@' \
  --ai-backend ollama --auto -o rooted-master.ged
```

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --root-person 'Jane Smith' \
  --ai-backend none --auto -o rooted-master.ged
```

Names must resolve uniquely. A GEDCOM pointer is preferable when two people
share a name.

## GEDCOM versions and website uploads

GEDCOM is a transfer standard, so separate Ancestry, Geni, and MyHeritage
conversion scripts should not be the starting point. These sites still have
different importer behavior for custom tags, media, source citations, and
newer dialect details.

The default output is GEDCOM 5.5.5:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --gedcom-version 5.5.5 --ai-backend none --auto -o master.ged
```

If a destination rejects that version declaration, create a 5.5.1 compatibility
export from the same source inputs:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --gedcom-version 5.5.1 --ai-backend none --auto \
  -o master-5.5.1.ged
```

For Geni, prefer the 5.5.1 export first: [Geni's GEDCOM
guidance](https://help.geni.com/hc/en-us/articles/229705167-How-can-I-export-my-GEDCOM)
identifies 5.5.1 as its standard export type, and [its
importer](https://help.geni.com/hc/en-us/articles/229705127-Can-I-import-a-GEDCOM-into-Geni)
is designed around a focus profile in the shared World Family Tree. [Ancestry](https://ancestry.my.site.com/FrCa/articles/en_US/Support_Site/Uploading-and-Downloading-Trees)
and [MyHeritage](https://www.myheritage.com/help/en/articles/12852096-how-do-i-upload-import-a-gedcom-file-to-my-family-site-on-myheritage)
also accept GEDCOM uploads, but media binaries are not embedded in GEDCOM text;
preserve the original media separately.

Upload a copy, inspect a sample of people/families/sources in the destination,
and keep the generated master GEDCOM as the portable source of truth. Add a
site-specific compatibility profile only if real importer testing identifies
a reproducible vendor quirk; maintaining three speculative converters would
increase data-loss risk.

## Merge safety and review

- Cross-file candidates are blocked by name/year keys before fuzzy scoring,
  reducing unnecessary AI calls and memory use.
- Very high deterministic matches can merge without AI.
- Uncertain pairs go to the configured adjudicator.
- AI suggestions can choose a canonical displayed value only from source
  values; conflicts remain as alternative event blocks.
- Remote errors and invalid JSON fail closed: both people are retained.
- Output is written atomically; 5.5.5 output is validated before replacement.
- Input code is never evaluated and unsafe deserialization is not used.

Omit `--auto` to receive interactive confirmation for lower-confidence AI
decisions. For unattended jobs, keep `--auto` and review verbose routing logs:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend auto --auto --verbose -o master.ged
```

## Troubleshooting

`OPENROUTER_MANAGEMENT_KEY is not set` or an unverifiable-balance message:

- Create a management key and add it to `.env` for strict checking, or choose
  `--credit-check best-effort` if a per-key check is sufficient for your risk
  policy.

`OPENAI_API_KEY is not set`, `GEMINI_API_KEY ... is not set`, or
`OPENROUTER_API_KEY is not set`:

- Confirm `.env` is in the repository root and that the relevant value is not
  blank. Existing shell environment values override `.env`.

Ollama connection failure:

```bash
ollama serve
ollama pull llama3.1
```

Importer rejects the GEDCOM:

- Retry with `--gedcom-version 5.5.1`.
- Check the logged validation error and destination-specific import report.
- Preserve the original inputs and master output while investigating.

## Dependency installation

Install the tested dependency ranges from the repository root:

```bash
python -m pip install -r requirements.txt
```

The relevant packages are:

```text
python-gedcom==1.1.0
rapidfuzz>=3.14.1
python-dateutil>=2.9.0
python-dotenv>=1.1.0,<2
openai>=2.45.0,<3
google-genai>=2.12.0,<3
openrouter>=0.11.37,<0.12
```

Run checks with:

```bash
python -m pytest -q tests/test_gedcom_merge.py
python -m py_compile tools/gedcom_merge.py
```
