# Keys, spend limits and usage

Docs: https://docs.deapi.ai/api-keys.md. All paths are on `https://api.deapi.ai`.

## Roles

| Role | Can | Use for |
|---|---|---|
| `api` | inference, quotes, job status, its own state (`GET /api/v2/key`, `/key/usage`) | applications, integrations, one key per project or customer |
| `admin` | everything, plus billing (`/account/*`) and key management (`/keys*`) | back-office tools only; keep it like a password |

Keys made before roles existed are ADMIN keys. An API key gets 403 `insufficient_permissions`
on billing and key endpoints. ADMIN keys are created, changed and deleted only in the dashboard;
over the API you can create only `api` keys.

## Restrictions on a key

| Restriction | Effect |
|---|---|
| `expires_at` | the key stops working after this date (401 `key_expired`) |
| `limit` (USD) + `reset_interval` (`none`, `daily`, `weekly`, `monthly`) | hard ceiling on spend per window; above it jobs get 402 `key_limit_exceeded`, nothing is created or charged |
| `ip_whitelist` (up to 20 IPv4/IPv6 addresses or CIDR ranges) | calls from other addresses get 403 `ip_not_allowed` |

- Windows are anchored to the key's creation time, not the calendar.
- Changing the limit does not reset what was spent in the current window. A refunded job gives
  its cost back to the limit.
- Limits are ceilings, not budgets: all keys together may exceed the balance.
- **Keys with an IP allowlist do not work on `oai.deapi.ai`.**
- Up to 100 active keys per account (422 `active_key_limit_reached` beyond that).

## Check the calling key

`GET /api/v2/key` (any role) returns `name`, `preview` (`sk-dp-<id>-...<last characters>`),
`role`, `status` (`active`, `revoked`, `expired`), `expires_at`, `limit`, `reset_interval`,
`limit_used`, `limit_remaining`, `limit_resets_at` and the allowlist. Never the secret. Call it
before a large batch to see whether the limit leaves room.

## Manage keys (ADMIN)

| Route | Action |
|---|---|
| `GET /api/v2/keys` | list (no secrets) |
| `POST /api/v2/keys` | create an `api` key: `name`, optional `expires_at`, `limit`, `reset_interval`, `ip_whitelist` |
| `GET /api/v2/keys/{id}` | one key |
| `PATCH /api/v2/keys/{id}` | change limit, window, expiry, allowlist, or `active: false` / `true` |
| `DELETE /api/v2/keys/{id}` | delete permanently (usage history stays) |
| `GET /api/v2/keys/{id}/usage` | usage of one key |

- The secret is returned **once**, in the response to `POST /api/v2/keys`. Store it in your
  secret store immediately; never log it, print it or put it in a chat with an agent.
- Key creation has its own hourly limit per account (429 `rate_limited`,
  `X-RateLimit-Type: key-creation`, `Retry-After` up to an hour).
- Typical pattern: one key per end customer with a monthly limit, so one customer cannot spend
  the whole balance, plus usage per key for billing.

Create a key (ADMIN key in `DEAPI_ADMIN_KEY`, the new secret goes straight to your store):

```bash
curl -s https://api.deapi.ai/api/v2/keys \
  -H "Authorization: Bearer $DEAPI_ADMIN_KEY" \
  -H "Content-Type: application/json" -H "Accept: application/json" \
  -d '{"name": "customer-42", "limit": 20, "reset_interval": "monthly"}'
```

## Usage and cost

| Route | Role | `group_by` |
|---|---|---|
| `GET /api/v2/key/usage` | any | `day` (default), `hour`, `type`, `model` |
| `GET /api/v2/account/usage` | ADMIN | `day`, `hour`, `type`, `model`, `key` |
| `GET /api/v2/keys/{id}/usage` | ADMIN | like `/key/usage` |

- `from` (inclusive) and `to` (exclusive) are UTC ISO 8601 timestamps; default is the last
  7 days. Data is kept 90 days; an hourly breakdown covers at most 31 days.
- Response `data`: `from`, `to`, `group_by`, `currency` (`USD`), `totals` and `groups`, each with
  `cost` (net of refunds), `requests` and `errors`. Only groups with usage are listed.
- With `group_by=key`, a group's `key` is a key ID, `panel` (jobs run from the dashboard),
  `deleted` (deleted keys combined) or `null`.
- `limit_used` on a key and the usage report answer different questions (current window vs the
  period you ask for); they will not match.

Example: `assets/usage_report.py` prints cost per model for the calling key, or per key for the
account with an ADMIN key:

```bash
python assets/usage_report.py --group-by model --days 30
python assets/usage_report.py --account --group-by key --days 7    # ADMIN key
```

## Billing (ADMIN)

- `GET /api/v2/account/balance` - balance.
- `GET /api/v2/account` - balance, currency and automatic top-up settings.
- `GET /api/v2/account/payments` - top-ups, newest first.

The per-job cost is also in `price` of the job status and of the `job.completed` webhook, which
is usually the easiest place to record it next to your own records.
