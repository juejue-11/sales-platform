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
test("embedded Pi SDK runs a real SSE tool loop with only business tools", async () => {
  const fake = await startFakeServer((body, response) => {
    if (fake.requests.length === 1) {
      streamResponse(response, [
        assistantToolCallChunk(),
        { id: "chatcmpl-test-tools", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 31, completion_tokens: 9, total_tokens: 40 } },
      ]);
      return;
    }
    const messages = body.messages as Array<Record<string, unknown>>;
    assert.ok(messages.some((message) => message.role === "tool"));
    streamResponse(response, [
      { id: "chatcmpl-test-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: "{\"decision\":\"recommend\",\"summary\":\"有明确需求\"}" }, finish_reason: null }] },
      { id: "chatcmpl-test-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 48, completion_tokens: 12, total_tokens: 60 } },
    ]);
  });
  try {
    const result = await runRunner(requestFor(fake.url));
    const resultLine = result.lines.find((line) => line.type === "result");
    assert.ok(resultLine);
    assert.deepEqual(JSON.parse(String(resultLine.content)), { decision: "recommend", summary: "有明确需求" });
    assert.deepEqual(resultLine.usage, { model_calls: 2, tool_calls: 3, input_tokens: 79, output_tokens: 21 });
    const first = fake.requests[0];
    assert.equal(first.headers.authorization, "Bearer request-key");
    const tools = first.body.tools as Array<{ function: { name: string } }>;
    assert.deepEqual(tools.map((tool) => tool.function.name), ["read_project_records", "read_company_capability", "read_related_records"]);
    assert.ok(!JSON.stringify(first.body).match(/\b(?:bash|read|write|edit|grep|find|ls)\b/iu));
    const firstMessages = first.body.messages as Array<Record<string, unknown>>;
    assert.equal(firstMessages[0].role, "system");
    assert.match(String(firstMessages[0].content), /decision/iu);
    const stages = result.lines.filter((line) => line.type === "progress").map((line) => line.stage);
    assert.deepEqual(stages, ["started", "model", "tool_start", "tool_start", "tool_start", "tool_end", "tool_end", "tool_end", "model", "completed"]);
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("reading task uses the no-tool protocol and preserves its task marker", async () => {
  const fake = await startFakeServer((body, response) => {
    assert.ok(body.tools === undefined || (Array.isArray(body.tools) && body.tools.length === 0));
    const messages = body.messages as Array<Record<string, unknown>>;
    assert.ok(messages.some((message) => String(message.content || "").includes("采购资料阅读")));
    streamResponse(response, [
      {
        id: "chatcmpl-reading-final",
        object: "chat.completion.chunk",
        created: 1,
        model: "screening-test-model",
        choices: [{ index: 0, delta: { role: "assistant", content: '{"version":1,"summary":"reading"}' }, finish_reason: null }],
      },
      {
        id: "chatcmpl-reading-final",
        object: "chat.completion.chunk",
        created: 1,
        model: "screening-test-model",
        choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
        usage: { prompt_tokens: 5, completion_tokens: 3, total_tokens: 8 },
      },
    ]);
  });
  try {
    const result = await runRunner(requestFor(fake.url, { task_kind: "reading" }));
    const resultLine = result.lines.find((line) => line.type === "result");
    assert.ok(resultLine);
    assert.equal(resultLine.task_kind, "reading");
    assert.deepEqual(JSON.parse(String(resultLine.content)), { version: 1, summary: "reading" });
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("official DeepSeek reading disables thinking in the outgoing payload", async () => {
  const fake = await startFakeServer((body, response) => {
    streamResponse(response, [
      {
        id: "chatcmpl-reading-deepseek",
        object: "chat.completion.chunk",
        created: 1,
        model: "deepseek-flash",
        choices: [{ index: 0, delta: { role: "assistant", content: '{"version":1,"summary":"reading"}' }, finish_reason: "stop" }],
      },
    ]);
  });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (input, init) => originalFetch(String(input).replace("https://api.deepseek.com", fake.url), init);
  try {
    const input = requestFor("https://api.deepseek.com", {
      task_kind: "reading",
      limits: { max_turns: 6, max_tool_calls: 12, timeout_ms: 10_000, max_output_tokens: 16_384 },
    });
    (input.config as Record<string, unknown>).model = "deepseek-flash";
    const result = await runRequest(input as unknown as RuntimeRequest, () => {});
    assert.equal(result.type, "result");
    assert.equal(result.task_kind, "reading");
    assert.equal(result.content, '{"version":1,"summary":"reading"}');
    assert.equal(fake.requests.length, 1);
    const body = fake.requests[0].body;
    assert.deepEqual(body.thinking, { type: "disabled" });
    assert.equal(body.reasoning_effort, undefined);
    assert.deepEqual(body.response_format, { type: "json_object" });
    assert.ok(body.tools === undefined || (Array.isArray(body.tools) && body.tools.length === 0));
  } finally {
    globalThis.fetch = originalFetch;
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("embedded Pi SDK preserves interleaved RPC tool indexes through execution", async () => {
  const expectedArguments = new Map<string, Record<string, unknown>>([
    ["fetch_project_documents", { record_id: 7 }],
    ["search_project_sources", {
      purpose: "check",
      query: "equipment",
      source_ids: [],
      limit: 1,
      offset: 0,
      published_after: null,
      published_before: null,
    }],
  ]);
  const received: Record<string, unknown>[] = [];
  const fake = await startFakeServer((body, response) => {
    if (fake.requests.length === 1) {
      streamResponse(response, interleavedRpcToolCallChunks());
      return;
    }
    const messages = body.messages as Array<Record<string, unknown>>;
    const toolMessages = messages.filter((message) => message.role === "tool");
    assert.equal(toolMessages.length, 2);
    assert.ok(toolMessages.every((message) => typeof message.tool_call_id === "string"));
    streamResponse(response, [
      {
        id: "chatcmpl-interleaved-final",
        object: "chat.completion.chunk",
        created: 1,
        model: "screening-test-model",
        choices: [{
          index: 0,
          delta: { role: "assistant", content: '{"decision":"verify","summary":"two RPC reads completed","reasons":[],"unknowns":[],"next_actions":[],"options":[]}' },
          finish_reason: "stop",
        }],
        usage: { prompt_tokens: 20, completion_tokens: 20, total_tokens: 40 },
      },
    ]);
  });
  try {
    const result = await runDuplexRunner(
      duplexRequestFor(fake.url),
      (frame, child) => {
        const tool = String(frame.tool);
        assert.ok(expectedArguments.has(tool), `unexpected RPC tool ${tool}`);
        assert.deepEqual(frame.arguments, expectedArguments.get(tool));
        received.push(frame);
        child.stdin?.write(JSON.stringify({
          type: "tool_response",
          id: frame.id,
          ok: true,
          data: tool === "fetch_project_documents"
            ? { documents: [], procurement: {} }
            : { status: "ok", items: [] },
        }) + "\n");
      },
      DUPLEX_HARNESS_TIMEOUT_MS,
    );
    assert.equal(received.length, 2);
    assert.deepEqual(new Set(received.map((frame) => frame.tool)), new Set(expectedArguments.keys()));
    assert.equal(fake.requests.length, 2);
    const resultLine = result.lines.at(-1);
    assert.equal(resultLine?.type, "result");
    assert.equal((resultLine?.usage as Record<string, unknown>)?.tool_calls, 2);
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("optional company material keeps a name-only profile unevaluated and separates preferences", async () => {
  const fake = await startFakeServer((body, response) => {
    if (fake.requests.length === 1) {
      const system = String((body.messages as Array<Record<string, unknown>>)[0]?.content);
      assert.match(system, /customer_preferences.*仅调整关注重点/u);
      assert.match(system, /company_profile_present=false/u);
      streamResponse(response, [
        assistantCompanyToolCallChunk(),
        { id: "chatcmpl-test-company-tool", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 20, completion_tokens: 4, total_tokens: 24 } },
      ]);
      return;
    }
    const messages = body.messages as Array<Record<string, unknown>>;
    const toolMessage = messages.find((message) => message.role === "tool");
    assert.ok(toolMessage);
    const toolResult = JSON.parse(String(toolMessage?.content)) as Record<string, unknown>;
    assert.deepEqual(toolResult.company_capability_card, { company_name: "仅名称公司" });
    assert.equal(toolResult.company_profile_present, false);
    assert.deepEqual(toolResult.customer_preferences, { target_keywords: ["储能"], region_preferences: "华东" });
    assert.equal(toolResult.preference_scope, "customer_attention_only");
    assert.match(String(toolResult.preference_note), /不证明/u);
    streamResponse(response, [
      { id: "chatcmpl-test-company-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: '{"decision":"recommend","summary":"正文支持市场关注","demand_summary":"储能项目","reasons":[],"unknowns":["企业能力未评估"],"next_actions":["核实项目条件"],"options":[]}' }, finish_reason: null }] },
      { id: "chatcmpl-test-company-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 20, completion_tokens: 20, total_tokens: 40 } },
    ]);
  });
  try {
    const result = await runRunner(requestFor(fake.url, {
      context: {
        project: { id: 7, title: "储能项目", records: [{ id: "record-1", body_text: "采购储能系统" }] },
        company_capability_card: { company_name: "仅名称公司", target_keywords: ["储能"], region_preferences: "华东" },
        related_projects: [],
      },
    }));
    assert.equal(fake.requests.length, 2);
    assert.equal(JSON.parse(String(result.lines.at(-1)?.content)).decision, "recommend");
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("an empty capability card remains unevaluated even when customer preferences are present", async () => {
  const fake = await startFakeServer((body, response) => {
    if (fake.requests.length === 1) {
      streamResponse(response, [
        assistantCompanyToolCallChunk(),
        { id: "chatcmpl-test-empty-company", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 12, completion_tokens: 3, total_tokens: 15 } },
      ]);
      return;
    }
    const toolMessage = (body.messages as Array<Record<string, unknown>>).find((message) => message.role === "tool");
    assert.ok(toolMessage);
    const toolResult = JSON.parse(String(toolMessage?.content)) as Record<string, unknown>;
    assert.deepEqual(toolResult.company_capability_card, {});
    assert.equal(toolResult.company_profile_present, false);
    assert.deepEqual(toolResult.customer_preferences, { target_keywords: ["破碎机"], region_preferences: "西北" });
    streamResponse(response, [
      { id: "chatcmpl-test-empty-company-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: '{"decision":"consider","summary":"项目可继续做市场核实","demand_summary":"正文有设备需求","reasons":[],"unknowns":["企业资料未提供"],"next_actions":[],"options":[]}' }, finish_reason: null }] },
      { id: "chatcmpl-test-empty-company-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 12, completion_tokens: 20, total_tokens: 32 } },
    ]);
  });
  try {
    const result = await runRunner(requestFor(fake.url, {
      context: {
        project: { id: 7, title: "破碎机项目", records: [{ id: "record-1", body_text: "采购破碎机" }] },
        company_capability_card: {},
        customer_preferences: { target_keywords: ["破碎机"], region_preferences: "西北" },
        related_projects: [],
      },
    }));
    assert.equal(fake.requests.length, 2);
    assert.equal(JSON.parse(String(result.lines.at(-1)?.content)).decision, "consider");
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("company preferences are available as analysis focus without becoming capability evidence", async () => {
  const fake = await startFakeServer((body, response) => {
    if (fake.requests.length === 1) {
      const userMessages = (body.messages as Array<Record<string, unknown>>).filter((message) => message.role === "user");
      assert.ok(userMessages.some((message) => JSON.stringify(message.content).includes("客户关注方向")));
      streamResponse(response, [
        assistantCompanyToolCallChunk("储能"),
        { id: "chatcmpl-test-company-focus", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 20, completion_tokens: 4, total_tokens: 24 } },
      ]);
      return;
    }
    const toolMessage = (body.messages as Array<Record<string, unknown>>).find((message) => message.role === "tool");
    assert.ok(toolMessage);
    const toolResult = JSON.parse(String(toolMessage?.content)) as Record<string, unknown>;
    assert.deepEqual(toolResult.company_capability_card, { supported_products: ["储能系统"] });
    assert.equal(toolResult.company_profile_present, true);
    assert.deepEqual(toolResult.customer_preferences, {
      target_keywords: ["储能"],
      region_preferences: ["华东"],
      customer_role: "矿山采购方",
      equipment_categories: "破碎筛分",
      mineral_types: "铁矿",
      demand_focus: "交付周期",
    });
    assert.equal(toolResult.topic, "储能");
    streamResponse(response, [
      { id: "chatcmpl-test-company-focus-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: '{"decision":"consider","summary":"按储能关注偏好聚焦市场线索","demand_summary":"储能系统需求","reasons":[],"unknowns":[],"next_actions":[],"options":[]}' }, finish_reason: null }] },
      { id: "chatcmpl-test-company-focus-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 20, completion_tokens: 20, total_tokens: 40 } },
    ]);
  });
  try {
    const result = await runRunner(requestFor(fake.url, {
      messages: [
        { role: "system", content: "Return JSON with decision, summary, reasons, unknowns, next_actions, and options." },
        { role: "user", content: "请根据正文和客户关注方向分析市场线索。" },
      ],
      context: {
        project: { id: 7, title: "储能项目", records: [{ id: "record-1", body_text: "采购储能系统" }] },
        company_capability_card: { supported_products: ["储能系统"] },
        customer_preferences: {
          target_keywords: ["储能"],
          region_preferences: ["华东"],
          customer_role: "矿山采购方",
          equipment_categories: "破碎筛分",
          mineral_types: "铁矿",
          demand_focus: "交付周期",
        },
        company_profile_present: true,
        related_projects: [],
      },
    }));
    assert.equal(fake.requests.length, 2);
    assert.equal(JSON.parse(String(result.lines.at(-1)?.content)).decision, "consider");
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("SDK failures are returned as a redacted protocol error", async () => {
  const fake = await startFakeServer((_body, response) => {
    response.writeHead(500, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "provider rejected secret request-key" }));
  });
  const traceDir = await mkdtemp(join(tmpdir(), "screening-trace-"));
  const previousTraceDir = process.env.SCREENING_AI_TRACE_DIR;
  process.env.SCREENING_AI_TRACE_DIR = traceDir;
  try {
    const result = await runRunner(requestFor(fake.url));
    const errorLine = result.lines.at(-1);
    assert.deepEqual(errorLine, { type: "error", code: "MODEL_ERROR", message: "The model request failed." });
    assert.ok(!JSON.stringify(result.lines).includes("request-key"));
    const files = (await readdir(traceDir)).filter((name) => name.startsWith("screening-") && name.endsWith(".json"));
    assert.equal(files.length, 1);
    const trace = JSON.parse(await readFile(join(traceDir, files[0]), "utf8")) as Record<string, unknown>;
    assert.equal(trace.code, "MODEL_ERROR");
    assert.ok(!JSON.stringify(trace).includes("request-key"));
    assert.match(String((trace.sdk_message as Record<string, unknown>)?.errorMessage), /provider rejected/iu);
    const metadata = trace.request_metadata as Record<string, unknown>;
    assert.equal(metadata.model, "screening-test-model");
    assert.equal(metadata.max_tokens, 512);
    assert.deepEqual(metadata.message_roles, ["system", "user"]);
    assert.ok((metadata.tool_names as string[]).includes("read_project_records"));
    assert.ok(!Object.hasOwn(metadata, "headers"));
  } finally {
    if (previousTraceDir === undefined) delete process.env.SCREENING_AI_TRACE_DIR;
    else process.env.SCREENING_AI_TRACE_DIR = previousTraceDir;
    await rm(traceDir, { recursive: true, force: true });
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("HTTP 400 provider errors retain a redacted MODEL_ERROR trace", async () => {
  const fake = await startFakeServer((_body, response) => {
    response.writeHead(400, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "invalid payload for request-key" }));
  });
  const traceDir = await mkdtemp(join(tmpdir(), "screening-trace-"));
  const previousTraceDir = process.env.SCREENING_AI_TRACE_DIR;
  process.env.SCREENING_AI_TRACE_DIR = traceDir;
  try {
    const result = await runRunner(requestFor(fake.url));
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "MODEL_ERROR", message: "The model request failed." });
    const files = (await readdir(traceDir)).filter((name) => name.startsWith("screening-") && name.endsWith(".json"));
    assert.equal(files.length, 1);
    const trace = JSON.parse(await readFile(join(traceDir, files[0]), "utf8")) as Record<string, unknown>;
    assert.equal(trace.code, "MODEL_ERROR");
    assert.ok(!JSON.stringify(trace).includes("request-key"));
    assert.match(String((trace.sdk_message as Record<string, unknown>)?.errorMessage), /invalid payload/iu);
  } finally {
    if (previousTraceDir === undefined) delete process.env.SCREENING_AI_TRACE_DIR;
    else process.env.SCREENING_AI_TRACE_DIR = previousTraceDir;
    await rm(traceDir, { recursive: true, force: true });
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("turn limits stop before another SSE request", async () => {
  const fake = await startFakeServer((_body, response) => {
    streamResponse(response, [
      assistantToolCallChunk(),
      { id: "chatcmpl-limit", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
    ]);
  });
  try {
    const result = await runRunner(requestFor(fake.url, { limits: { max_turns: 1, max_tool_calls: 12, timeout_ms: 10_000, max_output_tokens: 512 } }));
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "TURN_LIMIT", message: "The model turn limit was exceeded." });
    assert.equal(fake.requests.length, 1);
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("normalizes a configured chat completions URL before the SDK appends its path", async () => {
  const fake = await startFakeServer((_body, response) => {
    streamResponse(response, [
      { id: "chatcmpl-no-tools", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: "{\"decision\":\"verify\"}" }, finish_reason: null }] },
      { id: "chatcmpl-no-tools", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
    ]);
  });
  try {
    const input = requestFor(fake.url);
    (input.config as Record<string, unknown>).base_url = `${fake.url}/v1/chat/completions`;
    const result = await runRunner(input);
    assert.equal(result.lines.at(-1)?.type, "result");
    assert.equal(fake.requests[0].url, "/v1/chat/completions");
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("length-terminated JSON-looking output is not reported as completed", async () => {
  const fake = await startFakeServer((_body, response) => {
    streamResponse(response, [
      { id: "chatcmpl-length", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: '{"decision":"recommend"}' }, finish_reason: null }] },
      { id: "chatcmpl-length", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: {}, finish_reason: "length" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
    ]);
  });
  const traceDir = await mkdtemp(join(tmpdir(), "screening-trace-"));
  const previousTraceDir = process.env.SCREENING_AI_TRACE_DIR;
  process.env.SCREENING_AI_TRACE_DIR = traceDir;
  try {
    const result = await runRunner(requestFor(fake.url));
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "OUTPUT_TOKEN_LIMIT", message: "The model output token limit was reached." });
    assert.deepEqual(result.lines.find((line) => line.type === "usage")?.usage,
      { model_calls: 1, tool_calls: 0, input_tokens: 1, output_tokens: 1 });
    assert.ok(!result.lines.some((line) => line.type === "result"));
    assert.ok(!result.lines.some((line) => line.type === "progress" && line.stage === "completed"));
    const files = (await readdir(traceDir)).filter((name) => name.startsWith("screening-") && name.endsWith(".json"));
    assert.equal(files.length, 1);
    const trace = JSON.parse(await readFile(join(traceDir, files[0]), "utf8")) as Record<string, unknown>;
    assert.equal(trace.code, "OUTPUT_TOKEN_LIMIT");
    assert.equal(trace.provider_chunk_count, 2);
    assert.deepEqual(trace.finish_reasons, ["length"]);
    const providerEvents = trace.provider_events as Array<Record<string, unknown>>;
    assert.equal((providerEvents.at(-1)?.choices as Array<Record<string, unknown>>)[0]?.finish_reason, "length");
    const sdkMessage = trace.sdk_message as Record<string, unknown>;
    assert.equal(sdkMessage.stopReason, "length");
    assert.equal(sdkMessage.rawStopReason, "length");
    assert.ok(!JSON.stringify(trace).includes("request-key"));
  } finally {
    if (previousTraceDir === undefined) delete process.env.SCREENING_AI_TRACE_DIR;
    else process.env.SCREENING_AI_TRACE_DIR = previousTraceDir;
    await rm(traceDir, { recursive: true, force: true });
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("embedded Pi SDK preserves a large nested report through a final content brace", async () => {
  const report = {
    decision: "recommend",
    summary: 'Nested } text with a quote " and a backslash \\ stays inside JSON.',
    demand_summary: "equipment\nprocurement",
    reasons: Array.from({ length: 24 }, (_, index) => ({
      text: `Reason ${index} contains } and \\"quoted\\" text`,
      evidence: Array.from({ length: 4 }, (_, evidenceIndex) => ({
        record_id: index + 1,
        quote: `row ${evidenceIndex}: } \\ \\"quoted\\"`,
        source: { section: `section-${index}`, page: evidenceIndex + 1 },
      })),
    })),
    unknowns: ["attachment } details", 'quoted "value" and slash \\'],
    next_actions: Array.from({ length: 16 }, (_, index) => ({
      action: `verify-${index}`,
      checks: [`field-${index}`, `field-${index}-continued`],
    })),
    options: Array.from({ length: 8 }, (_, index) => ({
      label: `option-${index}`,
      table: {
        columns: ["name", "value", "note"],
        rows: Array.from({ length: 6 }, (_, row) => [
          `item-${row}`,
          `value } ${row}`,
          `literal \\"${row}\\" \\`,
        ]),
      },
    })),
    sections: Array.from({ length: 12 }, (_, index) => ({
      heading: `section-${index}`,
      paragraphs: [`first } paragraph`, `second \\"paragraph\\" \\`],
      nested: { levels: [{ level: 1, values: ["}", "\\", '"'] }, { level: 2, values: [index, true, null] }] },
    })),
  };
  const source = JSON.stringify(report);
  assert.ok(source.length > 8_000);
  assert.equal(source.at(-1), "}");
  const chunks: Record<string, unknown>[] = [];
  for (let offset = 0; offset < source.length - 1; offset += 113) {
    chunks.push({
      id: "chatcmpl-large-report",
      object: "chat.completion.chunk",
      created: 1,
      model: "screening-test-model",
      choices: [{
        index: 0,
        delta: {
          ...(offset === 0 ? { role: "assistant" } : {}),
          content: source.slice(offset, Math.min(offset + 113, source.length - 1)),
        },
        finish_reason: null,
      }],
    });
  }
  chunks.push({
    id: "chatcmpl-large-report",
    object: "chat.completion.chunk",
    created: 1,
    model: "screening-test-model",
    choices: [{ index: 0, delta: { content: "}" }, finish_reason: "stop" }],
    usage: { prompt_tokens: 20, completion_tokens: source.length, total_tokens: source.length + 20 },
  });
  const finalChoice = (chunks.at(-1)?.choices as Array<{ delta: { content: string }; finish_reason: string }>)[0];
  assert.equal(finalChoice.delta.content, "}");
  assert.equal(finalChoice.finish_reason, "stop");

  const fake = await startFakeServer((_body, response) => streamResponse(response, chunks));
  try {
    const result = await runRunner(requestFor(fake.url));
    const resultLine = result.lines.at(-1);
    assert.equal(resultLine?.type, "result");
    assert.equal(resultLine?.content, source);
    assert.equal(Buffer.byteLength(String(resultLine?.content), "utf8"), Buffer.byteLength(source, "utf8"));
    assert.deepEqual(JSON.parse(String(resultLine?.content)), report);
    assert.equal((JSON.parse(String(resultLine?.content)) as Record<string, unknown>).sections instanceof Array, true);
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});
