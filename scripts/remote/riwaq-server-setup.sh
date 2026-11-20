#!/usr/bin/env bash
# Prepare a rented GPU server for Riwaq: Ollama listening on loopback only, one open model pulled.
# Nothing here opens a public port. Riwaq reaches the server only through an SSH tunnel.
# Usage (on the server):  bash riwaq-server-setup.sh [model]      default model: qwen3:32b
set -euo pipefail
MODEL="${1:-qwen3:32b}"
case "$MODEL" in *[!A-Za-z0-9._:/-]*|"") echo "FAIL: model name is not accepted"; exit 2;; esac
STORE="${RIWAQ_OLLAMA_STORE:-/workspace/ollama}"
[ -d /workspace ] || STORE="$HOME/ollama"
mkdir -p "$STORE"
export OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS="$STORE" OLLAMA_NO_CLOUD=1 OLLAMA_KEEP_ALIVE=30m

if ! command -v ollama >/dev/null 2>&1; then
  echo "Installing Ollama from ollama.com ..."
  curl -fsSL --proto '=https' --tlsv1.2 https://ollama.com/install.sh -o /tmp/ollama-install.sh
  sh /tmp/ollama-install.sh
fi

up() { curl -fsS -m 3 http://127.0.0.1:11434/api/version >/dev/null 2>&1; }
if ! up; then
  nohup ollama serve >"$STORE/serve.log" 2>&1 &
  for _ in $(seq 1 60); do up && break; sleep 1; done
fi
up || { echo "FAIL: Ollama did not start; see $STORE/serve.log"; exit 1; }

# Refuse to continue if the model server answers on anything but loopback.
for ADDR in $(hostname -I 2>/dev/null || true); do
  case "$ADDR" in 127.*|::1) continue;; esac
  case "$ADDR" in *:*) URL="http://[$ADDR]:11434/api/version";; *) URL="http://$ADDR:11434/api/version";; esac
  if curl -fsS -m 3 "$URL" >/dev/null 2>&1; then
    echo "FAIL: Ollama answers on the non-loopback address $ADDR. It was stopped; nothing was pulled."; pkill -f 'ollama serve' || true; exit 1
  fi
done

echo "Pulling $MODEL into $STORE (this can take a long time) ..."
ollama pull "$MODEL"
if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi --query-gpu=name,memory.total --format=csv,noheader; else echo "WARNING: no NVIDIA GPU visible"; fi
echo "OK: $MODEL is ready. Ollama listens on 127.0.0.1:11434 only."
echo "Stop or terminate the server in the provider console when you finish; storage is billed while it exists."
