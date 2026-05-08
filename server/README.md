# transcribe.subunit.ai — Server

FastAPI wrapper around `faster-whisper` with `large-v3-turbo`. Runs as Docker container, GPU-enabled when available, falls back to CPU.

Designed to back the Synapse Voice "subunit-mode" (DSGVO-konforme Cloud-Variante).

## Endpoints

| Method | Path                | Notes                                                   |
|--------|---------------------|---------------------------------------------------------|
| `GET`  | `/`                 | Service info                                            |
| `GET`  | `/v1/health`        | Liveness + model-loaded flag                            |
| `POST` | `/v1/transcribe`    | `multipart/form-data` — `file`, `language` (default de) |

`POST /v1/transcribe` returns:

```json
{
  "text": "transcribed string",
  "language": "de",
  "duration_s": 4.2,
  "elapsed_s": 0.85,
  "model": "large-v3-turbo"
}
```

Optional auth: set `TRANSCRIBE_API_KEY` env. Clients pass it via `X-API-Key` header.

## Local test

```bash
cd server
docker compose up --build

# in another shell
curl -F "file=@sample.wav" -F "language=de" \
     http://localhost:8005/v1/transcribe
```

## Production deploy (subunit server)

1. Pick an API key, store in `~/subunit/subunit-engine/subunit-core/credentials/transcribe.json`
2. Append the `transcribe-api` service block to `~/subunit/subunit-engine/docker-compose.yml`
3. `docker compose up -d --build transcribe-api`
4. Cloudflare Zero Trust → Tunnels → subunit-server → Public Hostname:
   - Subdomain: `transcribe`, Domain: `subunit.ai`
   - Service: `http://localhost:8005`
   - Path: leave empty (full proxy)
5. Verify: `curl -H "X-API-Key: …" https://transcribe.subunit.ai/v1/health`
6. Update `~/.config/synapse-voice/config.json`:
   - `subunit_endpoint: "https://transcribe.subunit.ai/v1/transcribe"`
   - Optional: `subunit_api_key: "…"` (would need a small edit in `synapse_voice/transcriber/subunit.py` to pass header)

## Resource expectations

- `large-v3-turbo` int8 on CPU: ~2.5 GB RAM, ~0.7× realtime
- `large-v3-turbo` float16 on GPU: ~3 GB VRAM, ~5× realtime
- Cold-start: ~3 s (model already pre-downloaded into the image)
