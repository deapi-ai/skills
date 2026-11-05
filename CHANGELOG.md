# Changelog

## 2.0.0 - Unreleased

### Added
- Second skill, `deapi-api`, for building deAPI into applications (native API v2, webhooks,
  OpenAI SDKs on `https://oai.deapi.ai/v1`).
- Free price quote before every paid job, with a cost limit (`--max-cost`, `DEAPI_MAX_COST`,
  default 0.25 USD) and `--quote` to get the price without submitting anything.
- Stable exit codes, mapped from the API's `code` field (key, role, IP allow-list, balance,
  key spend limit, rate limits, suspended account, removed endpoint).
- `status` (with `--wait` and `--download`), `account` (key role and spend limit, balance,
  usage) and `doctor` commands; `job:<request_id>` as input to chain jobs.
- Model choice from the live catalog: standard models by default, partner models only on
  request; `WhisperLargeV3Ct2` by default for transcription; parameters fitted to each model's
  limits; text-to-speech language and voice checked against the model.
- Commands `audio-video`, `video-upscale`, `describe`, `music`, `boost` and `models`; voice
  cloning and voice design in `tts`; speakers and `--subtitles srt|vtt` in `transcribe`;
  reference images, videos and audio in `animate`; `--count` for images.
- Input checks: file type from the first bytes, size limits, refusal of files in hidden or
  sensitive folders (also through links and for `--text-file`, `--lyrics-file`,
  `--input-file` and output paths); image quotes by size without uploading.
- Quotes that flags cannot lower: a file's measured length wins over `--duration`, links other
  than TikTok are priced from the link, and the prompt booster fee of `--enhance` is part of
  the cost check for images and videos. A limit above the default shows as `max_cost_raised`.
- The API key is never sent along an HTTP redirect (the script and the code examples).
- Downloads keep a media extension, stop above 1 GB and leave no partial file; with `--count`,
  results that finished before a failure are reported in `completed_results`.
- Reference docs for the script (`commands.md`, `recipes.md`, `models.md`, `network.md`) and
  for MCP clients (`mcp.md`); tested examples for the native API, webhooks and the OpenAI SDKs
  in `deapi-api/assets`.
- Repository checks: frontmatter rules, the Agent Skills reference validator, OpenAPI
  conformance, MCP tool names, doc examples parsed by the script's own parser, leak scan
  (files, commit messages, PR text, gitleaks); offline unit tests on Python 3.9 and 3.12,
  Linux and Windows; the `deapi-api` examples with Node.js, curl and pinned OpenAI SDKs; CI.

### Changed
- `stt` is now `transcribe` (old name kept as an alias), `balance` is now `account` (alias kept).
- Results are saved in `--out-dir` and named after the request ID; text results are saved to a
  file and previewed in the JSON output. Files are never overwritten.
- Configuration by flags only; `DEAPI_BASE_URL` and `DEAPI_POLL_*` are gone. `--base-url`
  accepts only `https://*.deapi.ai`.
- Paid requests are retried only on HTTP 429; a server error or dropped connection while
  submitting exits with code 9 instead of risking a double charge.
- An HTTP 403 without a JSON body (a proxy, firewall or CDN block) is exit code 1, not
  "account suspended".
- Long text for `tts` is refused with the model's limit (no automatic splitting); the docs say
  to quote every part and ask the user when the total is above the limit.
- `metadata` in the SKILL.md frontmatter uses block YAML (accepted by the reference validator).

### Removed
- `deapi-audio` skill (API v1). Use `deapi tts` and `deapi transcribe`.
- The `--scale` option of `upscale` (the API has no such field for images).
