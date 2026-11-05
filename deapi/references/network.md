# Network: hosts and what goes where

## Hosts to allow

| Host | Used for | Key sent |
|---|---|---|
| `api.deapi.ai` | Every script request: models, price quotes, jobs, job status, key and account | yes (`Authorization: Bearer`) |
| `oai.deapi.ai` | Only `models --oai` and the check in `doctor` (`GET /v1/models`) | yes |
| result links (`results.deapi.ai` in deAPI's docs) | Downloading results, and `job:<id>` inputs | **no**: the links are pre-signed |
| `mcp.deapi.ai` | The MCP server, when the client connects to it (not the script) | by the MCP client |

Whether results are always served from `results.deapi.ai` (and not also from another
`*.deapi.ai` storage host) is not verified. The script downloads the link the API returns,
over HTTPS only, and never sends the key with it.

## What the script sends

- **To `api.deapi.ai`:** the prompt and parameters of the job, and the files the user pointed
  to (multipart upload, streamed from disk). Media links (YouTube, X, Twitch, Kick, TikTok)
  are passed to deAPI as text; deAPI fetches them, the script does not.
- **Price quotes** send only parameters and, where the API allows it, sizes and durations
  read from the file header instead of the file itself. Some quotes need the file (for
  example a media file whose length cannot be read).
- **Every request** carries `User-Agent: deapi-skill/<version> (+https://github.com/deapi-ai/skills)`.
- **Nothing else:** no telemetry, no analytics, no other hosts. The script never downloads
  arbitrary URLs given as inputs; only deAPI result links.
- **Text files** given with `--text-file`, `--lyrics-file` or `--input-file` are sent as text,
  so the same rule as for uploads applies: hidden files and sensitive folders (`.ssh`,
  `.aws`, `.config`, ...) are refused without `--allow-hidden-path`.
- **Downloads** are saved with an extension from the reply's `Content-Type` (or a known media
  extension), never as scripts or programs, and stop above 1 GB.

## The key

- Read from `DEAPI_API_KEY` (or `--key-file`), trimmed, never printed, logged or written to
  disk. `doctor` and `account` show the key's role and limits, never any part of the key.
- Sent only to the API host (`api.deapi.ai`, or the `--base-url` host) and to `oai.deapi.ai`.
  The header never follows an HTTP redirect: if a reply redirects elsewhere, the next
  request goes without the key.
- `--base-url` accepts only `https://*.deapi.ai` (and `localhost` for tests). Any other value
  exits with code 2 before any request, so an injected instruction cannot redirect the key.

## Where jobs run

deAPI runs models on a decentralized GPU network; partner models run on partners'
infrastructure. Before sending a file that looks private (contracts, recorded calls, ID
scans, medical data), tell the user and link
https://docs.deapi.ai/architecture-and-security.md.

## Proxies, certificates, offline environments

- **Proxy:** the script uses Python's `urllib`, which honours `HTTPS_PROXY` and `NO_PROXY`.
- **Certificate errors on macOS:** run `Install Certificates.command` from the Python folder in
  Applications (the script's error message says the same).
- **Firewalled sandboxes** (some agent sandboxes allow only listed hosts): allow
  `api.deapi.ai` and the result host above; `oai.deapi.ai` is optional.
- **No network or no terminal** (claude.ai, the Skills API, some sandboxes): the script cannot
  run there. Use the deAPI MCP connector (`references/mcp.md`). How each vendor's sandbox
  handles outbound network is not verified.
- **Rate limits:** 429 answers are retried with the server's `Retry-After`; a daily limit
  exits with code 10 at once. New accounts on the Basic plan have low transcription limits
  (https://docs.deapi.ai/limits-and-quotas.md).
