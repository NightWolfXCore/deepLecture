#!/bin/sh
set -eu
model_file=${GGUF_PATH:-/imports/model.gguf}
if [ ! -s "$model_file" ]; then
    echo 'Missing GGUF: place Qwen3 4B at storage/llm/model.gguf' >&2
    exit 1
fi
ollama serve &
server_pid=$!
cleanup() { kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true; }
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
attempt=0
until ollama list >/dev/null 2>&1; do
    kill -0 "$server_pid" 2>/dev/null || exit 1
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        echo 'Ollama did not start within 60 seconds' >&2
        exit 1
    fi
    sleep 1
done
echo 'Importing local GGUF as deeplecture-qwen3:4b'
ollama create deeplecture-qwen3:4b -f /config/Modelfile
ollama show deeplecture-qwen3:4b >/dev/null
echo 'Local model deeplecture-qwen3:4b is available'
wait "$server_pid"
