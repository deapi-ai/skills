# Webhooks

deAPI sends an HTTPS `POST` to your server when a job changes status. This is the
recommended way for backends to get results. Docs:
https://docs.deapi.ai/execution-modes-and-integrations/webhooks.md.

> **Warning: 10 consecutive failed deliveries, across all webhooks of the account, disable
> webhooks for the whole account** until someone re-enables them in the dashboard. Test with a
> receiver you control that already answers 2xx, never with a URL that does not exist yet.

## Configure

- **Per job:** add `webhook_url` (HTTPS only, up to 2048 characters) and optionally
  `webhook_secret` (32-255 characters) to any job request. The secret must come with the URL.
  A per-job URL is delivered even when the account has no webhook configured.
- **Account-wide:** URL and default secret in the dashboard
  (https://app.deapi.ai/settings/webhooks). There is no API for this setting.
- Without any secret the callback still arrives, **unsigned** (empty `X-DeAPI-Signature`).
  Reject empty signatures if you need authenticity.

```python
import secrets

webhook_secret = secrets.token_urlsafe(48)   # 64 characters; store it with the job
body = {
    "prompt": "a red fox in the snow", "model": "Flux1schnell",
    "width": 512, "height": 512, "steps": 4, "seed": 42,
    "webhook_url": "https://example.com/webhooks/deapi",
    "webhook_secret": webhook_secret,
}
```

## Events and headers

| Event | Transition |
|---|---|
| `job.processing` | `pending` → `processing` |
| `job.completed` | `processing` → `done` |
| `job.failed` | `processing` → `error` |

| Header | Value |
|---|---|
| `X-DeAPI-Event` | the event name |
| `X-DeAPI-Signature` | `sha256=<hex HMAC-SHA256>` |
| `X-DeAPI-Timestamp` | Unix seconds when it was sent |
| `X-DeAPI-Delivery-Id` | unique per delivery; use it to deduplicate |
| `User-Agent` | `DeAPI-Webhook/1.0` |

## Payloads

`job.completed`:

```json
{
  "event": "job.completed",
  "delivery_id": "c4d543d8-b52b-4f62-a6c1-5605111dd830",
  "timestamp": "2026-08-07T11:43:26+00:00",
  "data": {
    "job_request_id": "868543c4-9429-4434-972a-db27769c8a34",
    "status": "done",
    "previous_status": "processing",
    "job_type": "txt2img",
    "completed_at": "2026-08-07T11:43:26+00:00",
    "result_url": "https://results.deapi.ai/...",
    "processing_time_ms": 12000,
    "price": {"amount": 0.0234, "is_estimated": false}
  }
}
```

- `price` is what the job cost, in the balance's unit (USD): the same figure as
  `GET /api/v2/jobs/{id}`. `is_estimated: true` (partner models) means the charge has not
  settled; do not use it for invoicing. `price` can be `null`.
- `result_url` is pre-signed and expires: download it promptly, without `Authorization`.
  Alternative formats and inline text (`result`) are only in `GET /api/v2/jobs/{id}`.
- `job_type` is the inference type slug (`txt2img`, `img2img`, `txt2video`, …). Treat it as an
  open list.

`job.failed` carries `failed_at`, `error_code` (`WORKER_TIMEOUT`, `WORKER_ERROR`,
`PROCESSING_ERROR`), `error_reason` (open list), `retryable`, `refunded` and `price` before any
refund. Decide retries on `retryable`; read `GET /api/v2/jobs/{id}` for the final refund and
charge. `job.processing` carries `started_at`.

## Verify the signature

1. Take the **raw body bytes**, before any JSON parsing or re-serialisation.
2. Message = `<X-DeAPI-Timestamp> + "." + <raw body>`.
3. Expected = `"sha256=" + hex(HMAC_SHA256(secret, message))`.
4. Compare with `X-DeAPI-Signature` in constant time.
5. Reject timestamps more than 5 minutes from your clock (replay protection).

Use the secret that signed that job: the per-job `webhook_secret` you stored (look it up by
`data.job_request_id`), otherwise the account default.

Ready-made, tested code:
- `assets/verify_webhook.py` - `verify_signature(headers, raw_body, secret)`, a delivery log for
  deduplication, `handle_delivery()` and a minimal `http.server` receiver.
- `assets/verify_webhook.mjs` - `verifyWebhook({...})` and `handleDelivery()` for Node.js,
  with a `node:http` receiver.

Framework notes (the raw body is the usual mistake):

| Framework | Raw body |
|---|---|
| Express | `app.post(path, express.raw({type: "application/json"}), ...)`, then `req.body` is a `Buffer` |
| Flask | `request.get_data()` |
| FastAPI / Starlette | `await request.body()` |
| Django | `request.body` |
| Next.js route handler | `await req.text()` (do not call `req.json()` first) |

```python
# Flask
from flask import Flask, request

from verify_webhook import DeliveryLog, handle_delivery

app = Flask(__name__)
log = DeliveryLog()


@app.post("/webhooks/deapi")
def deapi_webhook():
    status, event = handle_delivery(request.headers, request.get_data(),
                                    secret=lookup_secret, log=log)
    if event is not None:
        enqueue(event)          # your queue or background task; keep the handler fast
    return "", status
```

`lookup_secret(job_request_id)` returns the secret you stored for that job (or a single
string for the account default); `enqueue` hands the event to your worker.

## Delivery rules and retries

- Answer **2xx within 10 seconds**. Do the work afterwards (queue, background task).
- Redirects (3xx) count as failures. Point the URL at the final address.
- Failed deliveries are retried up to 10 times over about 24 hours: immediately, then after
  1, 2, 5, 10 and 30 minutes, 1, 3, 6 and 12 hours. Then the delivery is marked failed.
- Every retry of a job's callback is signed with the same secret. Repeated deliveries are
  expected: deduplicate (below).

## Idempotency

- Keep a record of processed `X-DeAPI-Delivery-Id` values (a unique index in your database).
  A repeated ID gets 2xx and no second processing.
- Make the business action idempotent too, keyed by `job_request_id` and event, so that a
  webhook and a poll (or a WebSocket event) for the same job do not both act.
- Events may arrive out of order (`job.processing` after `job.completed` on a retry). Never move
  a job back from `done` or `error`.

## Testing

- Unit-test the receiver with a signature you compute yourself (the assets do this).
- For end-to-end tests expose a local receiver (for example with a tunnel) that already answers
  2xx, and send one cheap job with `webhook_url`. Public request bins are fine for looking at
  headers, but the payload contains a result link anyone with the URL can open.
- If webhooks stopped arriving, check the dashboard: the account switch may have been turned off
  by the circuit breaker.
