# About this public copy

This repository is the source of Riwaq, a local-first Arabic AI assistant (FastAPI backend, React interface, optional Tauri desktop shell), released under the MIT license.

What is not included here:

- `reports/`: run evidence from development. It contained vendored third-party packages and binaries, so it was left out. Links to `reports/…` in the documents will not resolve, and tests that read evidence files will not run from this copy.
- Built binaries (`desktop/src-tauri/binaries`). Build them with `scripts/desktop-build.ps1`.
- Models, container images, user data and keys. None of them were ever part of the repository.

Status: a personal project. It runs from source on Windows with a local model through Ollama. The installer is unsigned and there has been no independent security review. See `docs/CORE_ACCEPTANCE_REPORT.md` for what was verified and what was not.
