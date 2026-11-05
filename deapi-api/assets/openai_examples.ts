// deAPI through the official OpenAI Node SDK (npm install openai). Node.js 18+.
// Tested with openai 7.51.0; `video` needs a release that has client.videos (not 4.x).
//
//   DEAPI_API_KEY=sk-dp-... npx tsx openai_examples.ts models                      # free
//   DEAPI_API_KEY=sk-dp-... npx tsx openai_examples.ts image "a red fox" fox.png   # paid
//   ... speech TEXT OUT.mp3 | transcribe FILE | embed TEXT... | video PROMPT OUT.mp4 (paid)
//
// No price quotes on this API: quote the same job on the native API (POST .../price) when the
// cost matters. Model IDs are native deAPI IDs and depend on the account: see `models`.
// DEAPI_OAI_URL (default https://oai.deapi.ai/v1) may name another *.deapi.ai host or a local mock.

import { createReadStream } from "node:fs";
import { writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import OpenAI from "openai";

export function makeClient(): OpenAI {
  const baseURL = process.env.DEAPI_OAI_URL ?? "https://oai.deapi.ai/v1";
  const u = new URL(baseURL);
  const host = u.hostname.toLowerCase();
  const ok = (u.protocol === "https:" && host.endsWith(".deapi.ai")) ||
    (u.protocol === "http:" && (host === "127.0.0.1" || host === "localhost"));
  if (!ok) throw new Error(`refusing to send the API key to ${host}`);
  // A second client next to any OpenAI client of the app; keep the key on the server.
  return new OpenAI({ apiKey: process.env.DEAPI_API_KEY, baseURL, timeout: 900_000 });
}

export async function listModels(client: OpenAI): Promise<string[]> {
  const page = await client.models.list();
  return page.data.map((m) => m.id);
}

export async function image(client: OpenAI, prompt: string, out: string, model = "Flux1schnell"): Promise<string> {
  const result = await client.images.generate({
    model, prompt, size: "1024x1024", n: 1, response_format: "b64_json", // PNG; a url would expire
  });
  await writeFile(out, Buffer.from(result.data?.[0]?.b64_json ?? "", "base64"), { flag: "wx" });
  return out;
}

export async function speech(client: OpenAI, text: string, out: string, model = "Kokoro", voice = "af_nova"): Promise<string> {
  const audio = await client.audio.speech.create({
    model, input: text, response_format: "mp3",
    voice: voice as any, // native voice IDs are not in the SDK's voice enum
  });
  await writeFile(out, Buffer.from(await audio.arrayBuffer()), { flag: "wx" });
  return out;
}

export async function transcribe(client: OpenAI, path: string, model = "WhisperLargeV3Ct2"): Promise<string> {
  const result = await client.audio.transcriptions.create({
    model, file: createReadStream(path), response_format: "verbose_json",
  });
  return result.text;
}

export async function embed(client: OpenAI, texts: string[], model = "Bge_M3_FP16"): Promise<number[][]> {
  // At most 100 items per call, and each item counts as one request against the rate limit.
  const result = await client.embeddings.create({ model, input: texts });
  return result.data.map((d) => d.embedding);
}

export async function video(client: OpenAI, prompt: string, out: string, model = "Ltx2_3_22B_Dist_INT8",
                            pollMs = 5000): Promise<string> {
  // seconds and size are per-model values outside OpenAI's enums, hence the casts. The
  // Idempotency-Key makes a retried create return the same job instead of a second one.
  let job = await client.videos.create(
    { model: model as any, prompt, seconds: "5" as any, size: "768x768" as any },
    { headers: { "Idempotency-Key": randomUUID() } },
  );
  while (job.status !== "completed" && job.status !== "failed") {
    await new Promise((r) => setTimeout(r, pollMs));
    job = await client.videos.retrieve(job.id);
  }
  if (job.status === "failed") throw new Error(`video ${job.id} failed: ${JSON.stringify(job.error)}`);
  const content = await client.videos.downloadContent(job.id);
  await writeFile(out, Buffer.from(await content.arrayBuffer()), { flag: "wx" });
  return out;
}

async function main(argv: string[]): Promise<number> {
  const [command, ...args] = argv;
  const client = makeClient();
  switch (command) {
    case "models": console.log((await listModels(client)).join("\n")); return 0;
    case "image": console.log(await image(client, args[0], args[1])); return 0;
    case "speech": console.log(await speech(client, args[0], args[1])); return 0;
    case "transcribe": console.log(await transcribe(client, args[0])); return 0;
    case "embed": {
      const vectors = await embed(client, args);
      console.log(`${vectors.length} vectors of ${vectors[0]?.length ?? 0} dimensions`);
      return 0;
    }
    case "video": console.log(await video(client, args[0], args[1])); return 0;
    default:
      console.error("usage: openai_examples.ts models|image|speech|transcribe|embed|video ...");
      return 2;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; },
    (err) => { console.error(`error: ${err.message}`); process.exitCode = 1; });
}
