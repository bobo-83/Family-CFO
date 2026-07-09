# Model Manager

The one privileged sidecar in the stack (ADR 0013): mounts the Docker socket +
project directory and exposes a single validated operation — swap the served
AI models by running `scripts/swap-model.sh`. Strict `org/name` repo-id
validation; one swap at a time; internal Docker network only, never published.
The API (owner role) is the only caller. Remove it (`--scale model-manager=0`)
to fall back to the command-line swap flow (ADR 0012).

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```
