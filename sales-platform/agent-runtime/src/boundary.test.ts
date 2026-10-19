import assert from "node:assert/strict";
import { once } from "node:events";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { RuntimeBoundaryError, StreamBoundaryTrace } from "./json-boundary.js";
import { runRequest, type RuntimeRequest } from "./runner.js";
import {
  DUPLEX_HARNESS_TIMEOUT_MS,
  assistantCompanyToolCallChunk,
  assistantFetchToolCallChunk,
  assistantMalformedFetchToolCallChunk,
  assistantResearchToolCallChunk,
  assistantToolCallChunk,
  duplexRequestFor,
  interleavedRpcToolCallChunks,
  requestFor,
  runDuplexRunner,
  runRunner,
  startFakeServer,
  streamResponse,
} from "./test-helpers.js";
test("raw tool arguments are assembled by index and compared with the SDK object", () => {
  const trace = new StreamBoundaryTrace();
  trace.resetTurn();
  trace.captureProviderEvent({ choices: [{ delta: { tool_calls: [{ index: 0, id: "call-a", function: { name: "read_company_capability", arguments: '{"topic":"' } }] } }] });
  trace.captureProviderEvent({ choices: [{ delta: { tool_calls: [{ index: 1, id: "call-b", function: { name: "read_related_records", arguments: '{"project_id":7}' } }] } }] });
  trace.captureProviderEvent({ choices: [{ delta: { tool_calls: [{ index: 0, id: "call-a", function: { arguments: 'equipment"}' } }] } }] });

  const parsed = trace.readToolArguments("call-a", "read_company_capability", { topic: "equipment" });
  assert.deepEqual(parsed, { raw: '{"topic":"equipment"}', value: { topic: "equipment" } });
  const mismatch = trace.readToolArguments("call-b", "read_related_records", { project_id: "7" });
  assert.ok(mismatch instanceof RuntimeBoundaryError);
  assert.equal(mismatch.code, "TOOL_PARSER_ERROR");

  const finalTrace = new StreamBoundaryTrace();
  finalTrace.resetTurn();
  finalTrace.captureProviderEvent({ choices: [{ delta: { content: '{"decision":"verify"}' }, finish_reason: "stop" }] });
  finalTrace.setAssistantMessage({
    role: "assistant",
    content: [{ type: "text", text: '{"decision":"different"}' }],
    api: "openai-completions",
    provider: "fixture",
    model: "fixture",
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    stopReason: "stop",
    timestamp: Date.now(),
  });
  const contentMismatch = finalTrace.compareFinalContent('{"decision":"different"}');
  assert.ok(contentMismatch instanceof RuntimeBoundaryError);
  assert.equal(contentMismatch.code, "TOOL_PARSER_ERROR");
});

test("trace writes bounded provider head and tail and prunes only its own files", async () => {
  const trace = new StreamBoundaryTrace();
  trace.resetTurn();
  for (let index = 0; index <= 400; index += 1) {
    trace.captureProviderEvent({
      id: `provider-${index}`,
      choices: [{
        index: 0,
        delta: { content: `chunk-${index}`, ...(index === 0 ? { reasoning_content: "private stream reasoning" } : {}) },
        finish_reason: index === 400 ? "stop" : null,
      }],
    });
  }
  trace.captureRequestMetadata({
    model: "deepseek-flash",
    max_tokens: 16_384,
    reasoning_effort: "low",
    thinking: { type: "enabled" },
    response_format: { type: "json_object" },
    tool_choice: "auto",
    tools: [{ type: "function", function: { name: "read_project_records" } }],
    messages: [
      { role: "system", content: "private prompt text" },
      { role: "assistant", reasoning_content: "private request reasoning", tool_calls: [{ id: "call-1" }] },
    ],
    headers: { authorization: "request-key" },
  });
  const traceDir = await mkdtemp(join(tmpdir(), "screening-trace-"));
  const previousTraceDir = process.env.SCREENING_AI_TRACE_DIR;
  process.env.SCREENING_AI_TRACE_DIR = traceDir;
  try {
    await writeFile(join(traceDir, "keep-foreign.json"), "keep", "utf8");
    for (let index = 0; index < 101; index += 1) {
      await writeFile(join(traceDir, `screening-existing-${index}.json`), "{}", "utf8");
    }
    await trace.write("MODEL_FORMAT_ERROR", { fixture: true }, "request-key");
    const entries = await readdir(traceDir);
    assert.ok(entries.includes("keep-foreign.json"));
    const ownFiles = entries.filter((name) => name.startsWith("screening-") && name.endsWith(".json"));
    assert.ok(ownFiles.length <= 100);
    const generated = await Promise.all(ownFiles.map(async (name) => ({
      name,
      value: JSON.parse(await readFile(join(traceDir, name), "utf8")) as Record<string, unknown>,
    })));
    const payload = generated.find((entry) => entry.value.provider_chunk_count === 401)?.value;
    assert.ok(payload);
    assert.equal(payload.provider_event_dropped, 81);
    const metadata = payload.request_metadata as Record<string, unknown>;
    assert.equal(metadata.reasoning_mode, "low");
    assert.equal(metadata.thinking_mode, "enabled");
    assert.equal(metadata.reasoning_present, true);
    assert.equal(metadata.response_format && (metadata.response_format as Record<string, unknown>).type, "json_object");
    assert.ok(!Object.hasOwn(metadata, "headers"));
    const providerEvents = payload.provider_events as Array<Record<string, unknown>>;
    assert.equal(providerEvents.length, 320);
    assert.equal(providerEvents[0]?.id, "provider-0");
    assert.equal(providerEvents.at(-1)?.id, "provider-400");
    assert.equal((providerEvents.at(-1)?.choices as Array<Record<string, unknown>>)[0]?.finish_reason, "stop");
    const firstDelta = ((providerEvents[0]?.choices as Array<Record<string, unknown>>)[0]?.delta as Record<string, unknown>);
    assert.deepEqual(firstDelta.reasoning_content, { omitted: "thinking", chars: "private stream reasoning".length });
    assert.ok(!JSON.stringify(payload).includes("private stream reasoning"));
    assert.ok(!JSON.stringify(payload).includes("private request reasoning"));
  } finally {
    if (previousTraceDir === undefined) delete process.env.SCREENING_AI_TRACE_DIR;
    else process.env.SCREENING_AI_TRACE_DIR = previousTraceDir;
    await rm(traceDir, { recursive: true, force: true });
  }
});

test("incomplete raw tool JSON never reaches the host bridge", async () => {
  const fake = await startFakeServer((body, response) => {
    if (fake.requests.length === 1) {
      streamResponse(response, [assistantMalformedFetchToolCallChunk()]);
      return;
    }
    const toolMessage = (body.messages as Array<Record<string, unknown>>).find((message) => message.role === "tool");
    assert.match(String(toolMessage?.content), /invalid JSON|invalid_request/u);
    streamResponse(response, [
      { id: "malformed-fetch-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { content: '{"decision":"verify"}' }, finish_reason: "stop" }] },
    ]);
  });
  const traceDir = await mkdtemp(join(tmpdir(), "screening-trace-"));
  const previousTraceDir = process.env.SCREENING_AI_TRACE_DIR;
  process.env.SCREENING_AI_TRACE_DIR = traceDir;
  let hostCalls = 0;
  try {
    const result = await runDuplexRunner(duplexRequestFor(fake.url), () => { hostCalls += 1; });
    const final = result.lines.at(-1);
    assert.equal(final?.type, "result");
    assert.equal((final?.usage as Record<string, unknown>)?.tool_calls, 0);
    assert.equal(hostCalls, 0);
    assert.equal(fake.requests.length, 2);
  } finally {
    if (previousTraceDir === undefined) delete process.env.SCREENING_AI_TRACE_DIR;
    else process.env.SCREENING_AI_TRACE_DIR = previousTraceDir;
    await rm(traceDir, { recursive: true, force: true });
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("repeated incomplete tool JSON stops after one repair attempt", async () => {
  const fake = await startFakeServer((_body, response) => {
    streamResponse(response, [assistantMalformedFetchToolCallChunk()]);
  });
  const traceDir = await mkdtemp(join(tmpdir(), "screening-trace-"));
  const previousTraceDir = process.env.SCREENING_AI_TRACE_DIR;
  process.env.SCREENING_AI_TRACE_DIR = traceDir;
  let hostCalls = 0;
  try {
    const result = await runDuplexRunner(duplexRequestFor(fake.url), () => { hostCalls += 1; });
    assert.equal(result.lines.at(-1)?.code, "TOOL_ARGUMENT_JSON");
    assert.equal(hostCalls, 0);
    assert.equal(fake.requests.length, 2);
  } finally {
    if (previousTraceDir === undefined) delete process.env.SCREENING_AI_TRACE_DIR;
    else process.env.SCREENING_AI_TRACE_DIR = previousTraceDir;
    await rm(traceDir, { recursive: true, force: true });
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("a transport EOF retries once when no tool ran", async () => {
  const fake = await startFakeServer((_body, response) => {
    if (fake.requests.length === 1) {
      streamResponse(response, [
        { id: "eof-first", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { content: '{"decision":"verify"' }, finish_reason: null }] },
      ]);
      return;
    }
    streamResponse(response, [
      { id: "eof-second", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { content: '{"decision":"verify"}' }, finish_reason: "stop" }] },
    ]);
  });
  try {
    const result = await runRunner(requestFor(fake.url));
    assert.equal(result.lines.at(-1)?.type, "result");
    assert.equal(fake.requests.length, 2);
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

for (const rawContent of ['{"decision":"verify"', " \n\t ", ""]) {
test(`invalid final JSON (${JSON.stringify(rawContent)}) is traced while the draft remains available to the host`, async () => {
  const fake = await startFakeServer((_body, response) => {
    streamResponse(response, [
      { id: "bad-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { content: rawContent }, finish_reason: "stop" }] },
    ]);
  });
  const traceDir = await mkdtemp(join(tmpdir(), "screening-trace-"));
  const previousTraceDir = process.env.SCREENING_AI_TRACE_DIR;
  process.env.SCREENING_AI_TRACE_DIR = traceDir;
  try {
    const events: unknown[] = [];
    const result = await runRequest(requestFor(fake.url) as unknown as RuntimeRequest, (event) => events.push(event));
    assert.equal(result.content, rawContent.trim());
    assert.equal(result.finish_reason, "stop");
    assert.equal(fake.requests.length, 1);
    assert.ok(events.some((event) => (event as Record<string, unknown>).type === "progress"));
    const files = (await readdir(traceDir)).filter((name) => name.startsWith("screening-") && name.endsWith(".json"));
    assert.equal(files.length, 1);
    const trace = JSON.parse(await readFile(join(traceDir, files[0]), "utf8")) as Record<string, unknown>;
    assert.equal(trace.code, "MODEL_FORMAT_ERROR");
    assert.equal((trace.assembled as Record<string, unknown>).content, rawContent);
    assert.deepEqual(trace.finish_reasons, ["stop"]);
    assert.equal(typeof trace.run_id, "string");
    assert.ok(!JSON.stringify(trace).includes("reasoning_content"));
  } finally {
    if (previousTraceDir === undefined) delete process.env.SCREENING_AI_TRACE_DIR;
    else process.env.SCREENING_AI_TRACE_DIR = previousTraceDir;
    await rm(traceDir, { recursive: true, force: true });
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});
}
