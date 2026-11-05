// Verify and deduplicate deAPI webhook deliveries. Node.js 18+, no dependencies.
//
//   import { handleDelivery, DeliveryLog } from "./verify_webhook.mjs";
//   const log = new DeliveryLog();
//   // Express: app.post("/webhooks/deapi", express.raw({ type: "application/json" }), (req, res) => {
//   //   const { status, event } = handleDelivery(req.headers, req.body, secret, log);
//   //   res.sendStatus(status);            // answer within 10 s
//   //   if (event) queueForProcessing(event);
//   // });
//
// Minimal receiver for local tests:
//   DEAPI_WEBHOOK_SECRET=... node verify_webhook.mjs --port 8080
//
// Signature: X-DeAPI-Signature = "sha256=" + hex(HMAC_SHA256(secret, timestamp + "." + rawBody)).

import { createHmac, timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const TOLERANCE_SECONDS = 300;

function header(headers, name) {
  if (typeof headers.get === "function") return headers.get(name) ?? ""; // fetch Headers
  const lowered = name.toLowerCase();
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === lowered) return Array.isArray(value) ? value[0] : String(value ?? "");
  }
  return "";
}

/** The value deAPI puts in X-DeAPI-Signature. rawBody: Buffer or string. */
export function sign(secret, timestamp, rawBody) {
  const body = Buffer.isBuffer(rawBody) ? rawBody : Buffer.from(rawBody, "utf8");
  const hmac = createHmac("sha256", secret);
  hmac.update(`${timestamp}.`);
  hmac.update(body);
  return "sha256=" + hmac.digest("hex");
}

/** True when the delivery is signed with `secret` and its timestamp is fresh. */
export function verifyWebhook({ headers, rawBody, secret, now = Date.now() / 1000,
                                toleranceSeconds = TOLERANCE_SECONDS }) {
  const signature = header(headers, "x-deapi-signature").trim();
  const timestamp = header(headers, "x-deapi-timestamp").trim();
  if (!secret || !signature || !/^\d+$/.test(timestamp)) return false; // unsigned = rejected
  if (Math.abs(now - Number(timestamp)) > toleranceSeconds) return false;
  const expected = Buffer.from(sign(secret, timestamp, rawBody));
  const given = Buffer.from(signature);
  // timingSafeEqual throws on different lengths, so compare lengths first.
  return given.length === expected.length && timingSafeEqual(given, expected);
}

/** Remembers processed delivery IDs in memory. Use a unique database index in production. */
export class DeliveryLog {
  constructor(maxItems = 10000) {
    this.seen = new Set();
    this.maxItems = maxItems;
  }

  firstTime(deliveryId) {
    if (this.seen.has(deliveryId)) return false;
    this.seen.add(deliveryId);
    if (this.seen.size > this.maxItems) this.seen.delete(this.seen.values().next().value);
    return true;
  }
}

/**
 * Check one delivery. `secret` is a string, or a function jobRequestId -> secret when you send a
 * per-job webhook_secret. Returns { status, event }: 401 bad signature or stale, 400 not JSON,
 * 200 with event null for a duplicate, 200 with the event for a new delivery.
 */
export function handleDelivery(headers, rawBody, secret, log, now) {
  const body = Buffer.isBuffer(rawBody) ? rawBody : Buffer.from(String(rawBody), "utf8");
  let event;
  try {
    event = JSON.parse(body.toString("utf8"));
  } catch {
    return { status: 400, event: null };
  }
  if (!event || typeof event !== "object") return { status: 400, event: null };
  // The body is parsed only to find which secret applies; nothing in it is trusted yet.
  const key = typeof secret === "function" ? secret(event.data?.job_request_id ?? "") : secret;
  const args = { headers, rawBody: body, secret: key };
  if (now !== undefined) args.now = now;
  if (!verifyWebhook(args)) return { status: 401, event: null };
  const deliveryId = header(headers, "x-deapi-delivery-id") || event.delivery_id || "";
  if (deliveryId && !log.firstTime(deliveryId)) return { status: 200, event: null };
  return { status: 200, event };
}

/** Minimal receiver: answers at once, processes afterwards. */
export function serve(port, secret, host = "127.0.0.1", onEvent = defaultProcess) {
  const log = new DeliveryLog();
  const server = createServer((req, res) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const { status, event } = handleDelivery(req.headers, Buffer.concat(chunks), secret, log);
      res.writeHead(status, { "Content-Length": "0" });
      res.end();
      if (event) setImmediate(() => onEvent(event));
    });
  });
  server.listen(port, host, () => console.log(`listening on http://${host}:${port}/ (POST)`));
  return server;
}

function defaultProcess(event) {
  const data = event.data ?? {};
  const price = data.price ?? {};
  console.log(`${event.event} ${data.job_request_id} ${data.status} price=${price.amount}` +
    (price.is_estimated ? " (estimated)" : ""));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  const i = process.argv.indexOf("--port");
  const port = i > 0 ? Number(process.argv[i + 1]) : 8080;
  const secret = process.env.DEAPI_WEBHOOK_SECRET ?? "";
  if (secret.length < 32) {
    console.error("set DEAPI_WEBHOOK_SECRET (32-255 characters)");
    process.exit(2);
  }
  serve(port, secret);
}
