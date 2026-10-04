# Local models

Lens can run a chat model itself, so summaries, templates and the assistant work with no model server and nothing
leaving the machine. It uses [llama.cpp](https://github.com/ggml-org/llama.cpp)'s server and GGUF files from
Hugging Face.

Status: built. Planned: GPU builds of llama.cpp (CUDA, Vulkan) fetched by Lens, local embedding models.

## Use it

**Settings → Local model**:

1. Pick a model. The list shows each model's download size and the memory it needs (the weights, the context, and room
   for Lens). Models this machine can't run, or has no disk for, are greyed out. Small ones (Qwen2.5 0.5B, Llama 3.2
   1B, Qwen2.5 1.5B) run on a Raspberry Pi; 7B and 8B models want 16 GB or a GPU.
2. Turn on **Run a model here** and save.

Lens then, in the background:

- fetches llama.cpp's server, unless `llama-server` is already on PATH (Homebrew: `brew install llama.cpp`) or
  `local_llm.server` names it in archive.yaml. The release build for Linux x64, Linux arm64 and macOS (Metal on Apple
  Silicon) goes into `data_dir/bin/llama.cpp`;
- downloads the model into `data_dir/models/gguf`, resuming an interrupted download. Listed models are pinned to a
  revision and checked against their SHA-256;
- starts the server with its own random API key (not on the command line), and once it answers makes it the LLM
  provider. The provider that was set before is kept and put back when you turn the local model off.

The page shows each step (fetching llama.cpp, downloading with a percentage, starting, running) and the server's last
lines when something goes wrong. Downloaded models can be deleted there to free the disk.

Any other GGUF chat model on Hugging Face works too: set the model to `hf:<owner>/<repo>/<file>.gguf`. Gated models
need `HF_TOKEN` in the environment.

## Settings

| Setting | Default | |
| --- | --- | --- |
| `local_llm.enabled` | off | |
| `local_llm.model` | none | a listed id, or `hf:owner/repo/file.gguf` |
| `local_llm.use_as_provider` | on | off: it runs, and you point things at it yourself |
| `local_llm.context` | 4096 | tokens; more needs more memory |
| `local_llm.threads` | auto | |
| `local_llm.gpu_layers` | 999 | as many as fit on a GPU; 0 for CPU only |
| `local_llm.port` | 8091 | |
| `local_llm.host` | automatic | the address workers reach it at (in Docker, the container's address) |
| `local_llm.server` | none | llama-server's path; archive.yaml only |

One Lens process runs the server at a time (it holds a lease, like the Cloudflare tunnel). In Docker, workers in
other containers reach it at the container's address on the compose network.

Code: `fastapi_backend/app/domain/local_llm.py`.
