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
test("reserves the last allowed turn for a report without further tools", async () => {
  const fake = await startFakeServer((body, response) => {
    if (Array.isArray(body.tools) && body.tools.length > 0) {
      streamResponse(response, [assistantToolCallChunk()]);
    } else {
      streamResponse(response, [
        { id: "final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: '{"decision":"consider"}' }, finish_reason: "stop" }] },
      ]);
    }
  });
  try {
    const request = requestFor(fake.url);
    request.limits = { max_turns: 2 };
    const result = await runRunner(request);
    assert.equal(result.lines.at(-1)?.type, "result", JSON.stringify({ events: result.lines, tools: fake.requests.map((r) => r.body.tools) }));
    assert.equal(fake.requests.length, 2);
    assert.ok(!Array.isArray(fake.requests[1].body.tools) || fake.requests[1].body.tools.length === 0);
    assert.equal(result.lines.at(-1)?.content, '{"decision":"consider"}');
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("default output budget leaves room for reasoning and a complete answer", async () => {
  const fake = await startFakeServer((body, response) => {
    const budget = Number(body.max_tokens);
    streamResponse(response, [
      { id: "chatcmpl-budget", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", reasoning_content: "分析项目资料", content: budget >= 16384 ? '{"decision":"verify"}' : '{"decision":' }, finish_reason: budget >= 16384 ? "stop" : "length" }], usage: { prompt_tokens: 32, completion_tokens: 6000, total_tokens: 6032, completion_tokens_details: { reasoning_tokens: 4500 } } },
    ]);
  });
  try {
    const input = requestFor(fake.url);
    delete input.limits;
    const result = await runRunner(input);
    assert.equal(result.lines.at(-1)?.type, "result");
    assert.equal(fake.requests[0].body.max_tokens, 16384);
    assert.equal(JSON.parse(String(result.lines.at(-1)?.content)).decision, "verify");
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("DeepSeek uses bounded thinking and JSON output through tool calls", async () => {
  const fake = await startFakeServer((body, response) => {
    assert.deepEqual(body.thinking, { type: "enabled" });
    assert.equal(body.reasoning_effort, "low");
    assert.deepEqual(body.response_format, { type: "json_object" });
    assert.equal(body.max_tokens, 16384);
    if (fake.requests.length === 1) {
      const chunk = assistantToolCallChunk();
      const choice = (chunk.choices as Array<{ delta: Record<string, unknown> }>)[0];
      choice.delta.reasoning_content = "先读取项目与能力资料。";
      streamResponse(response, [chunk]);
    } else {
      const messages = body.messages as Array<Record<string, unknown>>;
      assert.ok(messages.some((message) => message.role === "assistant" && message.reasoning_content === "先读取项目与能力资料。"));
      streamResponse(response, [
        { id: "chatcmpl-thinking-final", object: "chat.completion.chunk", created: 1, model: "deepseek-flash", choices: [{ index: 0, delta: { role: "assistant", reasoning_content: "核对采购资料后给出判断。", content: '{"decision":"verify"}' }, finish_reason: "stop" }] },
      ]);
    }
  });
  const originalFetch = globalThis.fetch;
  // Exercise the official-host compatibility path against the local fake server.
  globalThis.fetch = (input, init) => originalFetch(String(input).replace("https://api.deepseek.com", fake.url), init);
  try {
    const input = requestFor("https://api.deepseek.com", { limits: { max_turns: 6, max_tool_calls: 12, timeout_ms: 10_000, max_output_tokens: 16384 } });
    (input.config as Record<string, unknown>).model = "deepseek-flash";
    const result = await runRequest(input as unknown as RuntimeRequest, () => {});
    assert.equal(result.type, "result");
    assert.equal(result.content, '{"decision":"verify"}');
    assert.equal(fake.requests.length, 2);
  } finally {
    globalThis.fetch = originalFetch;
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("record tools expose fragment metadata without repeating body text", async () => {
  const fake = await startFakeServer((body, response) => {
    if (fake.requests.length === 1) {
      streamResponse(response, [
        assistantToolCallChunk(),
        { id: "chatcmpl-fragments", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
      ]);
      return;
    }
    const messages = body.messages as Array<Record<string, unknown>>;
    const toolPayloads = messages
      .filter((message) => message.role === "tool")
      .map((message) => JSON.parse(String(message.content)) as Record<string, unknown>);
    const recordsPayload = toolPayloads.find((payload) => Array.isArray(payload.records));
    assert.ok(recordsPayload);
    const records = recordsPayload.records as Array<Record<string, unknown>>;
    assert.equal(records[0]?.body_text, undefined);
    assert.deepEqual(records[0]?.evidence_fragments, [{ fragment_id: "frag-1", source_ref: "record-source" }]);
    const relatedPayload = toolPayloads.find((payload) => Array.isArray(payload.related_projects));
    assert.ok(relatedPayload);
    const relatedProjects = relatedPayload.related_projects as Array<Record<string, unknown>>;
    const relatedRecords = relatedProjects[0]?.records as Array<Record<string, unknown>>;
    assert.equal(relatedRecords[0]?.body_text, undefined);
    assert.deepEqual(relatedRecords[0]?.evidence_fragments, [{ fragment_id: "frag-2", source_ref: "related-source" }]);
    streamResponse(response, [
      { id: "chatcmpl-fragments-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: '{"decision":"verify"}' }, finish_reason: "stop" }] },
    ]);
  });
  try {
    const result = await runRunner(requestFor(fake.url, {
      context: {
        project: {
          id: 7,
          title: "储能项目",
          records: [{ id: "record-1", body_text: "完整正文不应重复发送", evidence_fragments: [{ fragment_id: "frag-1", source_ref: "record-source" }] }],
        },
        company_capability_card: { capabilities: ["储能系统集成"] },
        related_projects: [{
          id: "related-1",
          title: "候选关联",
          records: [{ id: "r-2", body_text: "关联完整正文不应重复发送", evidence_fragments: [{ fragment_id: "frag-2", source_ref: "related-source" }] }],
        }],
      },
    }));
    assert.equal(result.lines.at(-1)?.type, "result");
    assert.equal(fake.requests.length, 2);
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("DeepSeek format-only requests disable thinking and business tools", async () => {
  const fake = await startFakeServer((body, response) => {
    assert.deepEqual(body.thinking, { type: "disabled" });
    assert.deepEqual(body.response_format, { type: "json_object" });
    assert.ok(body.tools === undefined || (Array.isArray(body.tools) && body.tools.length === 0));
    streamResponse(response, [
      { id: "chatcmpl-format-only", object: "chat.completion.chunk", created: 1, model: "deepseek-flash", choices: [{ index: 0, delta: { content: '{"decision":"verify"}' }, finish_reason: "stop" }] },
    ]);
  });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (input, init) => originalFetch(String(input).replace("https://api.deepseek.com", fake.url), init);
  try {
    const input = requestFor("https://api.deepseek.com", {
      limits: { max_turns: 1, max_tool_calls: 0, timeout_ms: 10_000, max_output_tokens: 16_384 },
    });
    (input.config as Record<string, unknown>).model = "deepseek-flash";
    const result = await runRequest(input as unknown as RuntimeRequest, () => {});
    assert.equal(result.type, "result");
    assert.equal(fake.requests.length, 1);
  } finally {
    globalThis.fetch = originalFetch;
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("a single oversized SSE text chunk is aborted before completion", async () => {
  const oversized = "界".repeat(90_000);
  const fake = await startFakeServer((_body, response) => {
    streamResponse(response, [
      { id: "chatcmpl-oversized", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: oversized }, finish_reason: null }] },
      { id: "chatcmpl-oversized", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
    ]);
  });
  try {
    const result = await runRunner(requestFor(fake.url));
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "OUTPUT_TOKEN_LIMIT", message: "The model output token limit was reached." });
    assert.ok(!result.lines.some((line) => line.type === "result"));
    assert.ok(!result.lines.some((line) => line.type === "progress" && line.stage === "completed"));
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("API key echoed in final JSON is rejected without stdout or stderr leakage", async () => {
  const fake = await startFakeServer((_body, response) => {
    streamResponse(response, [
      { id: "chatcmpl-echo", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: '{"decision":"recommend","summary":"request-key"}' }, finish_reason: null }] },
      { id: "chatcmpl-echo", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
    ]);
  });
  try {
    const result = await runRunner(requestFor(fake.url));
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "MODEL_ERROR", message: "The model request failed." });
    assert.ok(!result.lines.some((line) => line.type === "result"));
    assert.ok(!JSON.stringify(result.lines).includes("request-key"));
    assert.ok(!result.stderr.includes("request-key"));
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

async function assertToolCap(cap: number, expectedReads: number, expectedErrors: number): Promise<void> {
  const fake = await startFakeServer((_body, response) => {
    streamResponse(response, [
      assistantToolCallChunk(),
      { id: "chatcmpl-cap", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
    ]);
  });
  try {
    const input = requestFor(fake.url, {
      limits: { max_turns: 6, max_tool_calls: cap, timeout_ms: 10_000, max_output_tokens: 512 },
    }) as unknown as RuntimeRequest;
    let reads = 0;
    const context = input.context as unknown as Record<string, unknown>;
    const project = context.project as Record<string, unknown>;
    Object.defineProperty(project, "records", {
      configurable: true,
      enumerable: true,
      get: () => {
        reads += 1;
        return [{ id: "record-1", body_text: "采购储能系统" }];
      },
    });
    Object.defineProperty(context, "company_capability_card", {
      configurable: true,
      enumerable: true,
      get: () => {
        reads += 1;
        return { capabilities: ["储能系统集成"] };
      },
    });
    Object.defineProperty(context, "related_projects", {
      configurable: true,
      enumerable: true,
      get: () => {
        reads += 1;
        return [{ id: "related-1", title: "候选关联", relation: "possible_duplicate", records: [] }];
      },
    });
    const events: Record<string, unknown>[] = [];
    await assert.rejects(
      runRequest(input, (event) => events.push(event as unknown as Record<string, unknown>)),
      (error: unknown) => error instanceof Error && error.message === "tool",
    );
    assert.equal(reads, expectedReads);
    assert.equal(fake.requests.length, 1);
    const toolEnds = events.filter((event) => event.type === "progress" && event.stage === "tool_end");
    assert.equal(toolEnds.length, 3);
    assert.equal(toolEnds.filter((event) => event.is_error === true).length, expectedErrors);
    assert.ok(toolEnds.filter((event) => event.is_error !== true).every((event) => !Object.hasOwn(event, "is_error")));
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
}

test("max_tool_calls=0 sends no business tools and leaves context unread", async () => {
  const fake = await startFakeServer((body, response) => {
    assert.ok(body.tools === undefined || (Array.isArray(body.tools) && body.tools.length === 0));
    streamResponse(response, [
      { id: "chatcmpl-cap-zero", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { content: '{"decision":"verify"}' }, finish_reason: "stop" }] },
    ]);
  });
  try {
    const input = requestFor(fake.url, {
      limits: { max_turns: 1, max_tool_calls: 0, timeout_ms: 10_000, max_output_tokens: 512 },
    }) as unknown as RuntimeRequest;
    let reads = 0;
    const context = input.context as unknown as Record<string, unknown>;
    const project = context.project as Record<string, unknown>;
    Object.defineProperty(project, "records", { configurable: true, enumerable: true, get: () => { reads += 1; return []; } });
    Object.defineProperty(context, "company_capability_card", { configurable: true, enumerable: true, get: () => { reads += 1; return {}; } });
    Object.defineProperty(context, "related_projects", { configurable: true, enumerable: true, get: () => { reads += 1; return []; } });
    const events: Record<string, unknown>[] = [];
    const result = await runRequest(input, (event) => events.push(event as unknown as Record<string, unknown>));
    assert.equal(result.type, "result");
    assert.equal(reads, 0);
    assert.equal(fake.requests.length, 1);
    assert.equal(events.filter((event) => event.type === "progress" && event.stage === "tool_end").length, 0);
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("max_tool_calls=1 allows only one business read", async () => {
  await assertToolCap(1, 1, 2);
});

test("deadline aborts a hanging provider without exposing its secret", async () => {
  const fake = await startFakeServer((_body, response) => {
    setTimeout(() => {
      if (response.destroyed || response.writableEnded) return;
      response.writeHead(500, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: "provider timed out with request-key" }));
    }, 5_000).unref();
  });
  try {
    const result = await runRunner(requestFor(fake.url, {
      // Leave room for SDK session initialization on Windows, so this tests
      // aborting an in-flight provider request rather than setup alone.
      limits: { max_turns: 6, max_tool_calls: 12, timeout_ms: 2_000, max_output_tokens: 512 },
    }));
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "TIMEOUT", message: "The runtime deadline was exceeded." });
    assert.equal(fake.requests.length, 1);
    assert.ok(!JSON.stringify(result.lines).includes("request-key"));
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});
