// One text-to-image job on the native deAPI API v2, end to end:
// model card -> free price quote -> cost ceiling -> submit -> poll -> download.
// Node.js 18+ (global fetch), no dependencies. Run with a TypeScript runner, e.g.
//   DEAPI_API_KEY=sk-dp-... npx tsx native_job.ts "a red fox in the snow" --quote
//   DEAPI_API_KEY=sk-dp-... node --experimental-strip-types native_job.ts "a red fox" --max-cost 0.01
// The key is sent only to the API host; result links are pre-signed and fetched without it.

import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const USER_AGENT = "deapi-example/2.0 (+https://docs.deapi.ai)";
const DEFAULT_MAX_COST = 0.25; // USD per job

type Json = Record<string, any>;

export class ApiError extends Error {
  status: number;
  code?: string;
  body: Json;
  headers: Headers;
  constructor(status: number, body: Json, headers: Headers) {
    super(`HTTP ${status}${body.code ? " " + body.code : ""} ${body.message ?? ""}`);
    this.status = status;
    this.code = body.code;
    this.body = body;
    this.headers = headers;
  }
}

export class JobFailed extends Error {
  job: Json;
  constructor(requestId: string, job: Json) {
    super(`job ${requestId} failed: ${job.error_reason ?? job.error_code} ` +
      `(retryable=${job.retryable}, refunded=${job.refunded})`);
    this.job = job;
  }
}

/** Allow only https://*.deapi.ai, or plain http on this machine for tests and mocks. */
export function checkedApiUrl(url: string): string {
  const u = new URL(url);
  const host = u.hostname.toLowerCase();
  const deapi = u.protocol === "https:" && (host === "deapi.ai" || host.endsWith(".deapi.ai"));
  const local = u.protocol === "http:" && (host === "127.0.0.1" || host === "localhost");
  if (!deapi && !local) throw new Error(`refusing to send the API key to ${host}`);
  return url.replace(/\/+$/, "");
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class Client {
  private apiKey: string;
  private apiUrl: string;

  constructor(apiKey: string | undefined, apiUrl = "https://api.deapi.ai") {
    if (!apiKey) throw new Error("DEAPI_API_KEY is not set");
    this.apiKey = apiKey;
    this.apiUrl = checkedApiUrl(apiUrl);
  }

  async request(method: string, path: string, body?: Json, query?: Record<string, string>,
                retries = 3): Promise<Json> {
    const url = this.apiUrl + path + (query ? "?" + new URLSearchParams(query).toString() : "");
    for (let attempt = 0; ; attempt++) {
      const res = await fetch(url, {
        method,
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          Accept: "application/json",
          "User-Agent": USER_AGENT,
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
        redirect: "error", // the API does not redirect; never carry the key to another URL
      });
      const text = await res.text();
      let parsed: Json = {};
      try { parsed = text ? JSON.parse(text) : {}; } catch { parsed = { message: text.slice(0, 200) }; }
      if (res.ok) return parsed;
      const error = new ApiError(res.status, parsed, res.headers);
      // 429 on the minute window is safe to retry; 5xx only on GET (a job POST may exist).
      const minute = (res.headers.get("X-RateLimit-Type") ?? "minute") === "minute";
      const retry = res.status === 429 ? minute : method === "GET" && res.status >= 500;
      if (attempt >= retries || !retry) throw error;
      const after = Number(res.headers.get("Retry-After"));
      await sleep(1000 * (Number.isFinite(after) && after > 0 ? Math.min(after, 60) : 2 ** attempt));
    }
  }

  async modelCard(inferenceType: string, slug?: string): Promise<Json> {
    const data = await this.request("GET", "/api/v2/models",
      undefined, { "filter[inference_types]": inferenceType, limit: "100" });
    const models: Json[] = data.data ?? [];
    const found = models.find((m) => (slug ? m.slug === slug : m.status === "standard_model"));
    if (!found) throw new Error(`no ${inferenceType} model ${slug ?? ""} is available to this key`);
    return found;
  }

  /** Free price quote: price (null when unknown) and whether it is an estimate. */
  async quote(path: string, body: Json): Promise<{ price: number | null; isEstimated: boolean }> {
    const data = await this.request("POST", path + "/price", body);
    const info = data.data ?? data; // the prompt booster answers {"price": ...} without "data"
    if (info.estimate_basis === "unavailable") return { price: null, isEstimated: true };
    return { price: info.price ?? null, isEstimated: Boolean(info.is_estimated) };
  }

  async submit(path: string, body: Json): Promise<string> {
    return (await this.request("POST", path, body)).data.request_id;
  }

  async wait(requestId: string, timeoutMs = 900_000, firstDelayMs = 2000, maxDelayMs = 10_000): Promise<Json> {
    let delay = firstDelayMs;
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const job = (await this.request("GET", "/api/v2/jobs/" + encodeURIComponent(requestId))).data;
      if (job.status === "done") return job;
      if (job.status === "error") throw new JobFailed(requestId, job);
      if (Date.now() > deadline) {
        throw new Error(`job ${requestId} still ${job.status}; check it later with GET /api/v2/jobs/${requestId}`);
      }
      await sleep(delay);
      delay = Math.min(delay * 1.5, maxDelayMs);
    }
  }
}

/** Save a result link. No Authorization header: the link is pre-signed. */
export async function download(url: string, path: string): Promise<string> {
  const res = await fetch(url, { headers: { "User-Agent": USER_AGENT } });
  if (!res.ok) throw new Error(`download failed: HTTP ${res.status}`);
  await writeFile(path, Buffer.from(await res.arrayBuffer()), { flag: "wx" });
  return path;
}

/** Request body built from the model card: defaults, limits and feature flags. */
export function imageBody(card: Json, prompt: string, width?: number, height?: number, seed?: number): Json {
  const limits = card.info?.limits ?? {};
  const features = card.info?.features ?? {};
  const defaults = card.info?.defaults ?? {};
  const step = limits.resolution_step ?? limits.dimension_multiple ?? 1;
  const size = (value: number | undefined, name: string): number => {
    let v = Math.trunc(value ?? defaults[name] ?? 512);
    const low = limits[`min_${name}`] ?? v;
    const high = limits[`max_${name}`] ?? v;
    v = Math.max(low, Math.min(high, v));
    return Math.max(low, v - (v % step));
  };
  const body: Json = {
    prompt, model: card.slug,
    width: size(width, "width"), height: size(height, "height"),
    seed: seed ?? Math.floor(Math.random() * 2 ** 31),
  };
  if (features.supports_steps) body.steps = defaults.steps ?? limits.min_steps ?? 4;
  if (features.supports_guidance) body.guidance = defaults.guidance ?? limits.min_guidance ?? 1;
  return body;
}

async function main(argv: string[]): Promise<number> {
  const flags = new Map<string, string | true>();
  const positional: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--quote") flags.set("quote", true);
    else if (a.startsWith("--")) flags.set(a.slice(2), argv[++i]);
    else positional.push(a);
  }
  const prompt = positional[0];
  if (!prompt) {
    console.error("usage: native_job.ts PROMPT [--quote] [--max-cost USD] [--model SLUG] [--out FILE]");
    return 2;
  }
  const maxCost = Number(flags.get("max-cost") ?? DEFAULT_MAX_COST);
  try {
    const client = new Client(process.env.DEAPI_API_KEY, process.env.DEAPI_API_URL ?? "https://api.deapi.ai");
    const model = flags.get("model");
    const card = await client.modelCard("txt2img", typeof model === "string" ? model : undefined);
    const body = imageBody(card, prompt);
    const path = "/api/v2/images/generations";
    const { price, isEstimated } = await client.quote(path, body);
    const summary = { model: body.model, quoted_price: price, is_estimated: isEstimated, max_cost: maxCost };
    if (flags.has("quote")) {
      console.log(JSON.stringify({ ...summary, quote_only: true }));
      return 0;
    }
    if (price === null || price > maxCost) {
      console.log(JSON.stringify({ ...summary, error: "quote above the cost ceiling or unknown" }));
      return 5;
    }
    const requestId = await client.submit(path, body);
    const job = await client.wait(requestId);
    const outFlag = flags.get("out");
    const out = await download(job.result_url, typeof outFlag === "string" ? outFlag : `${requestId}.png`);
    console.log(JSON.stringify({ ...summary, request_id: requestId, price: job.price, file: out }));
    return 0;
  } catch (err) {
    console.error(`error: ${(err as Error).message}`);
    return 1;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; });
}
