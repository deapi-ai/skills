# AGENTS.md

Guide for anyone, human or agent, changing this repository. It is public: everything you
commit is published.

## Layout

- `deapi/` - the "do it now" skill. `SKILL.md` (keep it under ~200 lines, rules first),
  `references/*.md` (one level deep), `scripts/deapi.py` plus the `scripts/deapi_lib/` package.
- `deapi-api/` - the "build it into an app" skill: `SKILL.md`, `references/`, `assets/`
  (code examples that CI runs).
- `tests/unit/` - offline tests; `tests/fakeapi.py` - in-process fake of the API;
  `tests/fixtures/` - synthetic data and the public OpenAPI snapshot.
- `tools/` - repository checks. `tools/run_checks.sh` runs all of them.

## Rules for the script

- Python 3.9+, standard library only. No `pip install` for users.
- All HTTP goes through `urllib.request.urlopen`, called through the module
  (`urllib.request.urlopen(...)`) so tests and wrappers can replace it.
- The API key comes only from `DEAPI_API_KEY` (or `--key-file`). It is sent only to the API host
  (`*.deapi.ai`); never to result links, never printed, logged or written to disk.
  Read variables one by one (`os.getenv`, `os.environ.get`), never `os.environ[...]` or the
  whole mapping, and never write an agent's secrets file path literally: agent skill
  scanners refuse such skills (`tools/check_skill_safety.py` checks it).
- Every paid command quotes first (`POST .../price`, free), compares the quote with the cost
  limit (`--max-cost`, `$DEAPI_MAX_COST`, default 0.25 USD) and supports `--quote`. The agent
  never raises the limit on its own. Its output (also `--quote`) carries `max_cost` and
  `max_cost_source`, the limit in force.
- Paid POSTs are retried only on HTTP 429. Exit codes are listed in `deapi_lib/core.py`.
- JSON on stdout, diagnostics on stderr. Results go to `--out-dir`, named after the request ID,
  never overwriting files.
- Model IDs, limits, languages and voices come from `GET /api/v2/models` at run time. Never
  hardcode model IDs except as preference hints.

### Adding or changing a command

Commands live in `deapi/scripts/deapi_lib/commands/<group>.py`. Each module defines
`register(subparsers)` and is picked up automatically (order: `ORDER`). Use the core:

```python
from .. import core

def register(sub):
    p = core.add_command(sub, "rmbg", "remove the background from an image")
    p.add_argument("source", metavar="IMAGE")
    p.set_defaults(func=cmd_rmbg)

def cmd_rmbg(ctx, args):
    part = core.resolve_input(ctx, args.source, "image", "image")
    model = ctx.pick_model("rmbg", args.model, args.allow_partner_models)
    fields, files = core.image_quote(part, model=model["slug"])
    job = core.Job("rmbg", "/api/v2/images/background-removals", model,
                   fields={"model": model["slug"]}, files=[part],
                   quote_fields=fields, quote_files=files, seed_field=None)
    return ctx.run(job, args)       # quote, cost limit, submit, wait, download
```

Price endpoints accept fewer fields than job endpoints; build the quote body from the
`*PriceRequestBody` schema of the spec. Add `SAMPLES` to the module so
`tools/check_openapi.py` checks your paths and fields against the spec.

## Tests and checks

Run them in Docker, without network:

```bash
docker build -t deapi-skills-test -f tests/Dockerfile tests
docker run --rm --network none -v "$PWD:/repo" -w /repo deapi-skills-test
```

- `run_checks.sh` runs the unit tests, `tests/test_deapi_api.py` (parts that need Node.js,
  curl or the `openai` packages skip without them; CI's `examples` job installs pinned
  versions) and the repository checks. CI also runs the Agent Skills reference validator
  (`skills-ref`) on both skills.
- Tests never call the real API. Use `tests/fakeapi.py`.
- Fixtures are synthetic: field names from the public spec, invented values. Never commit
  responses from a real account, generated results or logs of live calls.
- Use `sk-dp-0-TESTKEYNOTREAL` as the test key.

## Public repository hygiene

- No keys or tokens, no internal host names, no internal repository names, no task or ticket
  numbers, no machine paths, no personal names or e-mail addresses, no links to agent sessions.
  This applies to code, docs, tests, commit messages, branch names and PR text.
- Enable the hook once per clone: `git config core.hooksPath tools/git-hooks`.
- Before pushing: `python tools/scan_leaks.py` and `python tools/scan_leaks.py --git-meta`
  (it also warns about personal author and committer addresses; prefer a
  `users.noreply.github.com` address).
  Maintainers can add private patterns with `--extra-patterns FILE` (kept outside the repo);
  in CI they come from the optional `LEAK_PATTERNS` repository secret (one `label<TAB>regex` per line).
- English only in the repository.
