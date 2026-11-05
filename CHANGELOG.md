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
- `transcribe --duration SEC --quote` without a link or file: the price for a length alone,
  with `--platform` (`file`, `youtube`, `x`, `twitch`, `kick`, `tiktok`) priced the way
  deAPI prices that source. The skill tells the agent never to invent a link for a quote.
- Every paid command (not only `--quote`) prints `max_cost` and `max_cost_source`, the cost
  limit in force; the agent reports that value, not the documented default.
- `doctor` lists blocking issues in `problems`; `ok` is true when the script can run jobs,
  even with warnings such as an ADMIN key without a spend limit.
- `tools/check_skill_safety.py`: keeps out patterns that agent skill scanners block
  (a literal path to an agent's secrets file, `os.environ[...]`, `process.env[...]`,
  chat-role tags as placeholders).
- README: setting the key in Hermes Agent, and a deAPI chat model as the Hermes model.

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
- The `deapi` description: its first sentence names deAPI within the 57 characters that the
  Hermes skill index shows, everyday wording ("read text aloud, also in the user's own
  voice", "improve prompts"), "Use when ... unless they name another provider" and "Not for
  chat, summaries or other LLM text generation".
- OpenClaw: `requires.anyBins: [python3, python, py]` instead of `bins: [python3]`, so the
  skill is available on Windows, where Python is usually `python` or `py`.
- The script reads only `DEAPI_API_KEY` and `DEAPI_MAX_COST` from the environment, one by
  one; the docs, the script and the `deapi-api` examples no longer spell out the Hermes
  secrets file path or use `os.environ[...]`, which the Hermes skills hub scanner refused.
- `mcp__deapi__{tool}` instead of an angle-bracket placeholder, which some gateways reject
  as a chat-role tag.
- SKILL.md: one script call per command line (no `; echo $?`, chains or pipes; the exit code
  is in the JSON) with an example Claude Code permission rule; `--max-cost` after approval
  equals the approved quote exactly; `doctor` once per session before the first paid job and
  after key, role, balance or spend-limit errors.

### Removed
- `deapi-audio` skill (API v1). Use `deapi tts` and `deapi transcribe`.
- The `--scale` option of `upscale` (the API has no such field for images).
