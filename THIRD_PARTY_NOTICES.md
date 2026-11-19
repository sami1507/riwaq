# Third-party notices

The MIT license in `LICENSE` covers Riwaq's own source code. These bundled files keep their own licenses:

| Files | Source | License |
|---|---|---|
| `frontend/public/fonts/IBMPlex*.woff2` | IBM Plex — https://github.com/IBM/plex (see `frontend/public/fonts/manifest.json`) | SIL Open Font License 1.1 |
| `frontend/public/fonts/ReemKufi-*.woff2` | Reem Kufi — https://github.com/aliftype/reem-kufi (see `frontend/public/fonts/display-fonts.json`, `ReemKufi-OFL.txt`) | SIL Open Font License 1.1 |

Python and JavaScript dependencies are not vendored; they are installed from `requirements.lock.txt` and `frontend/package-lock.json` under their own licenses. Local models (for example through Ollama) and container images are downloaded by the user and are not part of this repository.
