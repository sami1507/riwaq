# Riwaq architecture and decisions

Current scope and evidence: [14 November audit](RIWAQ_AUDIT.md). Product goals and acceptance criteria: [unified requirements](RIWAQ_REQUIREMENTS.md).

## Local-first vertical slice

React 19 + TypeScript 5.9 + Vite 7 compile one static frontend. FastAPI serves those files and the API on loopback port 8765. Tauri 2 source is configured to wrap the same service/UI on Windows; OS4551 blocked the native build/run path, so this is not a verified desktop deliverable. SQLite WAL stores projects, messages, artifact versions, explicit memory and event records. A partial response is committed as each content chunk arrives; startup marks abandoned streams interrupted. Edits/retries branch rather than destroy history.

Python 3.12 was selected from installed interpreters for library/wheel and PyInstaller compatibility; the newer installed Python 3.15 is not the runtime. Exact Python/npm resolution is recorded in lock files. This workspace has no Git repository; the audit uses source manifests and local backups. Node 24.19.0 satisfies Vite 7 requirements. Tauri requires MSVC and Rust on Windows; the build report distinguishes the source configuration from actual build artifacts.

## AI and trust boundaries

Providers use an asynchronous iterator contract. Ollama is fixed to `http://127.0.0.1:11434`; remote/cloud model references are rejected. Cloud adapters use fixed HTTPS API domains, require hybrid mode and a user-selected provider, and never serve as fallback. HTTP clients ignore ambient proxy variables. OS keyring stores API keys; the browser and SQLite never receive them.

Only user-entered project instructions and reviewed memory are instructional context. File excerpts are untrusted, quoted data. The model receives no tools, credentials, execution interface, or authority to edit files. Citations are allowlisted against retrieved rows; links identify the available source, and do not prove every model claim is entailed. Unsupported markers are flagged. If retrieval yields no relevant excerpts, source-only chat returns a clear no-source message.

Private project files use Unicode/Arabic-normalized BM25. The separate opt-in public corpus combines BM25 with local embeddings and RRF, and deduplicates identical result text. Model/digest/dimension namespaces remain separate; no external embeddings or cloud fallback. Source rights/current versions are rechecked after provider validation and before dispatch. Relevant neighbors do not prove sufficient evidence.

HTML preformatted code retains indentation and blank lines under html-pre-v2; legacy-v1 replays older versions. Locations refer to stored extracted text. Context assembly budgets question, instructions, memory, whole excerpts and history before saving; its conservative UTF-8 bound is not a universal tokenizer. Output-limit finish reasons persist as interrupted. The concise-v2 prompt experiment was rejected; legacy-v1 stays default. See docs/QUALITY_2026-11-13.md.

PDF page references are actual pages; DOCX uses extracted paragraph lines. Scanned PDF OCR is unavailable. Parser limits are not OS isolation.

## Local service security model

This release has one local user per data directory. It binds to loopback, verifies Host and Origin and requires a non-simple `X-Riwaq-Client` header. Cross-origin pages cannot read or send authenticated API requests. This does not protect against another process running as the same OS user, nor make the local service suitable for internet hosting. Project IDs scope retrieval and memory. User-selected workspace grants authorize only that folder; secret paths, links, hardlinks and traversal are rejected. Apply/undo require explicit user confirmation and content hash checks.

There is no general code execution: no verified OS sandbox is available. Apply is limited to reviewed text edits. Screen capture, microphone and clipboard reading are not enabled. Clipboard writing only follows a Copy click.

## Model choice

Measured hardware: 31.84 GiB RAM, RTX 5060 8151 MiB VRAM. `qwen3:4b-instruct` Q4_K_M is 2,497,293,803 bytes. Chosen as a conservative 4B local baseline with room for an 8192-token configured context. It is text-only for this product. Published capabilities are not treated as a successful product integration test. Registry digest is recorded in reports/live-model.json.

The official model card is Apache 2.0 and documents multilingual/code use. The actual product smoke checks cover short Arabic comprehension, a simple Python function, source-grounded facts, and cancellation, not general benchmark parity with ChatGPT/Claude. Cloud providers, voice, vision and sync must be separately validated.

## Official references reviewed 2026-11-12

- Ollama chat streaming: https://docs.ollama.com/api/chat
- Ollama Windows requirements: https://docs.ollama.com/windows
- Ollama local-only settings and context: https://docs.ollama.com/faq
- Qwen model/license: https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507
- Model package tags: https://registry.ollama.com/library/qwen3/tags
- FastAPI streaming: https://fastapi.tiangolo.com/advanced/custom-response/
- Tauri prerequisites: https://v2.tauri.app/start/prerequisites/
- OpenAI Responses streaming: https://developers.openai.com/api/docs/guides/streaming-responses
- Anthropic streaming: https://platform.claude.com/docs/en/build-with-claude/streaming

No inference from documentation replaces a live test. See reports for measured outcomes.

## Partial local visual pipeline (2026-11-14)

The local API stores image assets, editable design specifications, exact Unicode text and versions in SQLite. Fixed trusted workers use local SD 1.5 with a safety checker and a browser Canvas text renderer. Actual generation, Arabic PNG rendering and process cancellation were checked; image constraints can still fail. No design UI or image LoRA training exists. Project snapshots exclude visual data and declare that limitation; full SQLite backups retain it. Worker limits and kill-on-close containment are not a general sandbox for untrusted programs. See the audit for tested formats, retention and quality limitations.
