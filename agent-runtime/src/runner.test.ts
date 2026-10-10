import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { runRequest, type RuntimeRequest } from "./runner.js";

interface CapturedRequest {
  body: Record<string, unknown>;
  headers: IncomingMessage["headers"];
  url: string | undefined;
}

type Responder = (body: Record<string, unknown>, response: ServerResponse) => void;

const here = dirname(fileURLToPath(import.meta.url));
const runnerPath = join(here, "runner.js");

function assistantToolCallChunk(): Record<string, unknown> {
  return {
    id: "chatcmpl-test-tools",
    object: "chat.completion.chunk",
    created: 1,
    model: "screening-test-model",
    choices: [
      {
        index: 0,
        delta: {
          role: "assistant",
          tool_calls: [
            { index: 0, id: "call-records", type: "function", function: { name: "read_project_records", arguments: "{}" } },
            { index: 1, id: "call-capability", type: "function", function: { name: "read_company_capability", arguments: "{}" } },
            { index: 2, id: "call-related", type: "function", function: { name: "read_related_records", arguments: "{}" } },
          ],
        },
        finish_reason: "tool_calls",
      },
    ],
  };
}

function streamResponse(response: ServerResponse, chunks: Record<string, unknown>[]): void {
  response.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    connection: "keep-alive",
  });
  for (const chunk of chunks) response.write(`data: ${JSON.stringify(chunk)}\n\n`);
  response.write("data: [DONE]\n\n");
  response.end();
}

async function startFakeServer(responder: Responder): Promise<{
  server: Server;
  url: string;
  requests: CapturedRequest[];
}> {
  const requests: CapturedRequest[] = [];
  const server = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk)));
    const text = Buffer.concat(chunks).toString("utf8");
    const body = JSON.parse(text) as Record<string, unknown>;
    requests.push({ body, headers: request.headers, url: request.url });
    responder(body, response);
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  assert.ok(address && typeof address === "object");
  return { server, url: `http://127.0.0.1:${address.port}`, requests };
}

async function runRunner(input: Record<string, unknown>): Promise<{ lines: Record<string, unknown>[]; stderr: string }> {
  const child = spawn(process.execPath, [runnerPath], {
    cwd: here,
    env: { ...process.env, OPENAI_API_KEY: "ambient-should-not-be-used" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const stdout: Buffer[] = [];
  const stderr: Buffer[] = [];
  child.stdout.on("data", (chunk: Buffer) => stdout.push(Buffer.from(chunk)));
  child.stderr.on("data", (chunk: Buffer) => stderr.push(Buffer.from(chunk)));
  child.stdin.end(JSON.stringify(input));
  const [code] = (await once(child, "close")) as [number | null];
  assert.equal(code, 0);
  const output = Buffer.concat(stdout).toString("utf8").trim();
  assert.ok(output, `runner produced no output; stderr=${Buffer.concat(stderr).toString("utf8")}`);
  return {
    lines: output.split(/\r?\n/u).map((line) => JSON.parse(line) as Record<string, unknown>),
    stderr: Buffer.concat(stderr).toString("utf8"),
  };
}

async function runDuplexRunner(
  input: Record<string, unknown>,
  onToolRequest: (frame: Record<string, unknown>, child: ReturnType<typeof spawn>) => void,
  timeoutMs = 5_000,
): Promise<{ lines: Record<string, unknown>[]; stderr: string }> {
  const child = spawn(process.execPath, [runnerPath], {
    cwd: here,
    env: { ...process.env, OPENAI_API_KEY: "ambient-should-not-be-used" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const stdout: Buffer[] = [];
  const stderr: Buffer[] = [];
  const lines: Record<string, unknown>[] = [];
  let pending = "";
  child.stdin.on("error", () => {});
  child.stdout.on("data", (chunk: Buffer) => {
    stdout.push(Buffer.from(chunk));
    pending += Buffer.from(chunk).toString("utf8");
    while (true) {
      const newline = pending.indexOf("\n");
      if (newline < 0) break;
      const raw = pending.slice(0, newline).replace(/\r$/u, "");
      pending = pending.slice(newline + 1);
      if (!raw.trim()) continue;
      const frame = JSON.parse(raw) as Record<string, unknown>;
      lines.push(frame);
      if (frame.type === "tool_request") onToolRequest(frame, child);
    }
  });
  child.stderr.on("data", (chunk: Buffer) => stderr.push(Buffer.from(chunk)));
  child.stdin.write(`${JSON.stringify(input)}\n`);
  return await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`duplex runner timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      assert.equal(code, 0, `runner exited with ${code}; stderr=${Buffer.concat(stderr).toString("utf8")}`);
      if (pending.trim()) lines.push(JSON.parse(pending) as Record<string, unknown>);
      resolve({ lines, stderr: Buffer.concat(stderr).toString("utf8") });
    });
  });
}

function requestFor(baseUrl: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    protocol: 1,
    config: { base_url: `${baseUrl}/v1`, model: "screening-test-model", api_key: "request-key" },
    messages: [
      { role: "system", content: "Return JSON with decision, summary, reasons, unknowns, next_actions, and options." },
      { role: "user", content: "请判断这个项目是否值得跟进。" },
    ],
    context: {
      project: { id: 7, title: "储能项目", records: [{ id: "record-1", body_text: "采购储能系统" }] },
      company_capability_card: { capabilities: ["储能系统集成"] },
      related_projects: [{ id: "related-1", title: "候选关联", relation: "possible_duplicate", records: [{ id: "r-2", body_text: "候选记录" }] }],
    },
    limits: { max_turns: 6, max_tool_calls: 12, timeout_ms: 10_000, max_output_tokens: 512 },
    ...overrides,
  };
}

function duplexRequestFor(baseUrl: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return requestFor(baseUrl, {
    protocol: 2,
    context: {
      project: {
        id: 7,
        title: "储能项目",
        records: [{ id: 7, body_text: "采购范围见附件，交付信息待核实。", original_url: "https://notice.example/7" }],
      },
      company_capability_card: { capabilities: ["储能系统集成"] },
      related_projects: [],
    },
    limits: { max_turns: 6, max_tool_calls: 12, timeout_ms: 2_000, max_output_tokens: 512 },
    ...overrides,
  });
}

function assistantFetchToolCallChunk(recordId: number): Record<string, unknown> {
  return {
    id: "chatcmpl-fetch-documents",
    object: "chat.completion.chunk",
    created: 1,
    model: "screening-test-model",
    choices: [
      {
        index: 0,
        delta: {
          role: "assistant",
          tool_calls: [
            {
              index: 0,
              id: "call-fetch-documents",
              type: "function",
              function: { name: "fetch_project_documents", arguments: JSON.stringify({ record_id: recordId }) },
            },
          ],
        },
        finish_reason: "tool_calls",
      },
    ],
  };
}

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

test("SDK failures are returned as a redacted protocol error", async () => {
  const fake = await startFakeServer((_body, response) => {
    response.writeHead(500, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "provider rejected secret request-key" }));
  });
  try {
    const result = await runRunner(requestFor(fake.url));
    const errorLine = result.lines.at(-1);
    assert.deepEqual(errorLine, { type: "error", code: "MODEL_ERROR", message: "The model request failed." });
    assert.ok(!JSON.stringify(result.lines).includes("request-key"));
  } finally {
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
  try {
    const result = await runRunner(requestFor(fake.url));
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "MODEL_ERROR", message: "The model request failed." });
    assert.ok(!result.lines.some((line) => line.type === "result"));
    assert.ok(!result.lines.some((line) => line.type === "progress" && line.stage === "completed"));
  } finally {
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
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "MODEL_ERROR", message: "The model request failed." });
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

test("max_tool_calls=0 blocks every business read before execution", async () => {
  await assertToolCap(0, 0, 3);
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
    }, 1_000);
  });
  try {
    const result = await runRunner(requestFor(fake.url, {
      limits: { max_turns: 6, max_tool_calls: 12, timeout_ms: 250, max_output_tokens: 512 },
    }));
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "TIMEOUT", message: "The runtime deadline was exceeded." });
    assert.equal(fake.requests.length, 1);
    assert.ok(!JSON.stringify(result.lines).includes("request-key"));
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("protocol 2 fetches trusted public documents through the duplex tool bridge", async () => {
  const fake = await startFakeServer((body, response) => {
    if (fake.requests.length === 1) {
      const tools = body.tools as Array<{ function: { name: string } }>;
      assert.deepEqual(tools.map((tool) => tool.function.name), [
        "read_project_records",
        "read_company_capability",
        "read_related_records",
        "fetch_project_documents",
      ]);
      const system = String((body.messages as Array<Record<string, unknown>>)[0]?.content);
      assert.match(system, /fetch_project_documents/u);
      assert.match(system, /投标截止时间不是交付时间/u);
      streamResponse(response, [
        assistantFetchToolCallChunk(7),
        { id: "chatcmpl-fetch-documents", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 10, completion_tokens: 4, total_tokens: 14 } },
      ]);
      return;
    }
    const messages = body.messages as Array<Record<string, unknown>>;
    const toolMessage = messages.find((message) => message.role === "tool");
    assert.ok(toolMessage);
    assert.match(String(toolMessage?.content), /doc-1/u);
    assert.match(String(toolMessage?.content), /采购破碎机/u);
    streamResponse(response, [
      { id: "chatcmpl-fetch-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: '{"decision":"verify","summary":"附件已读取","demand_summary":"采购破碎机","reasons":[{"text":"附件有采购事实","evidence":[{"record_id":7,"document_id":"doc-1","quote":"采购破碎机"}]}],"unknowns":[],"next_actions":[],"options":[]}' }, finish_reason: null }] },
      { id: "chatcmpl-fetch-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 20, completion_tokens: 30, total_tokens: 50 } },
    ]);
  });
  let hostCalls = 0;
  try {
    const result = await runDuplexRunner(duplexRequestFor(fake.url), (frame, child) => {
      hostCalls += 1;
      assert.equal(frame.tool, "fetch_project_documents");
      assert.deepEqual(frame.arguments, { record_id: 7 });
      child.stdin?.write(JSON.stringify({
        type: "tool_response",
        id: frame.id,
        ok: true,
        data: {
          documents: [{
            id: "doc-1", record_id: 7, name: "采购附件.pdf", url: "https://notice.example/doc-1.pdf",
            status: "parsed", pages: 1, sha256: "hash-doc-1", fetched_at: "2026-10-10T00:00:00+08:00",
            mime_type: "application/pdf", byte_size: 123, text: "采购破碎机", page_texts: [{ page: 1, text: "采购破碎机" }],
            items: [{ name: "破碎机", spec: "标准型", material: "钢", quantity: "2", unit: "台", delivery: "合同签订后 30 日", source_document_id: "doc-1", page: 1, quote: "采购破碎机" }],
          }],
          procurement: { items: [{ name: "破碎机", quantity: "2", unit: "台", source_document_id: "doc-1", page: 1, quote: "采购破碎机" }] },
        },
      }) + "\n");
    });
    assert.equal(hostCalls, 1);
    assert.equal(fake.requests.length, 2);
    assert.equal(result.lines.at(-1)?.type, "result");
    assert.ok(result.lines.some((line) => line.type === "progress" && line.stage === "tool_start" && line.tool === "fetch_project_documents"));
    assert.ok(result.lines.some((line) => line.type === "progress" && line.stage === "tool_end" && line.tool === "fetch_project_documents"));
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("protocol 2 returns a safe document failure to the model", async () => {
  const fake = await startFakeServer((body, response) => {
    if (fake.requests.length === 1) {
      streamResponse(response, [
        assistantFetchToolCallChunk(7),
        { id: "chatcmpl-fetch-failure", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
      ]);
      return;
    }
    const toolMessage = (body.messages as Array<Record<string, unknown>>).find((message) => message.role === "tool");
    assert.match(String(toolMessage?.content), /auth_required/u);
    streamResponse(response, [
      { id: "chatcmpl-fetch-failure-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: '{"decision":"verify","summary":"资料不可访问","demand_summary":"待核实","reasons":[],"unknowns":["附件需要公开访问"],"next_actions":["请人工核实"],"options":[]}' }, finish_reason: null }] },
      { id: "chatcmpl-fetch-failure-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 2, completion_tokens: 4, total_tokens: 6 } },
    ]);
  });
  try {
    const result = await runDuplexRunner(duplexRequestFor(fake.url), (frame, child) => {
      child.stdin?.write(JSON.stringify({ type: "tool_response", id: frame.id, ok: false, error: "auth_required" }) + "\n");
    });
    assert.equal(result.lines.at(-1)?.type, "result");
    assert.equal(fake.requests.length, 2);
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("protocol 2 rejects a record id outside the current project without an RPC request", async () => {
  const fake = await startFakeServer((body, response) => {
    if (fake.requests.length === 1) {
      streamResponse(response, [
        assistantFetchToolCallChunk(999),
        { id: "chatcmpl-fetch-unknown-record", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
      ]);
      return;
    }
    const toolMessage = (body.messages as Array<Record<string, unknown>>).find((message) => message.role === "tool");
    assert.match(String(toolMessage?.content), /record_not_in_project/u);
    streamResponse(response, [
      { id: "chatcmpl-fetch-unknown-record-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: { role: "assistant", content: '{"decision":"verify","summary":"资料不足","demand_summary":"待核实","reasons":[],"unknowns":["记录不属于当前项目"],"next_actions":[],"options":[]}' }, finish_reason: null }] },
      { id: "chatcmpl-fetch-unknown-record-final", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 2, completion_tokens: 4, total_tokens: 6 } },
    ]);
  });
  let hostCalls = 0;
  try {
    const result = await runDuplexRunner(duplexRequestFor(fake.url), () => { hostCalls += 1; });
    assert.equal(hostCalls, 0);
    assert.equal(fake.requests.length, 2);
    assert.equal(result.lines.at(-1)?.type, "result");
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("protocol 2 rejects an unknown host response safely", async () => {
  const fake = await startFakeServer((_body, response) => {
    streamResponse(response, [
      assistantFetchToolCallChunk(7),
      { id: "chatcmpl-fetch-unknown-response", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
    ]);
  });
  try {
    const result = await runDuplexRunner(duplexRequestFor(fake.url), (frame, child) => {
      child.stdin?.write(JSON.stringify({ type: "tool_response", id: frame.id, ok: false, error: "provider leaked arbitrary text" }) + "\n");
    });
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "RPC_ERROR", message: "The tool bridge failed." });
    assert.equal(fake.requests.length, 1);
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("protocol 2 rejects a host response for an unknown request id", async () => {
  const fake = await startFakeServer((_body, response) => {
    streamResponse(response, [
      assistantFetchToolCallChunk(7),
      { id: "chatcmpl-fetch-unknown-id", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
    ]);
  });
  try {
    const result = await runDuplexRunner(duplexRequestFor(fake.url), (_frame, child) => {
      child.stdin?.write(JSON.stringify({
        type: "tool_response", id: "rpc-unknown", ok: true,
        data: { documents: [], procurement: {} },
      }) + "\n");
    });
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "RPC_ERROR", message: "The tool bridge failed." });
    assert.equal(fake.requests.length, 1);
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("protocol 2 exits on host EOF while a tool response is pending", async () => {
  const fake = await startFakeServer((_body, response) => {
    streamResponse(response, [
      assistantFetchToolCallChunk(7),
      { id: "chatcmpl-fetch-eof", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
    ]);
  });
  try {
    const result = await runDuplexRunner(duplexRequestFor(fake.url), (_frame, child) => child.stdin?.end());
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "RPC_ERROR", message: "The tool bridge failed." });
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});

test("protocol 2 times out when the host does not answer a tool request", async () => {
  const fake = await startFakeServer((_body, response) => {
    streamResponse(response, [
      assistantFetchToolCallChunk(7),
      { id: "chatcmpl-fetch-timeout", object: "chat.completion.chunk", created: 1, model: "screening-test-model", choices: [], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
    ]);
  });
  try {
    const result = await runDuplexRunner(duplexRequestFor(fake.url, {
      limits: { max_turns: 6, max_tool_calls: 12, timeout_ms: 150, max_output_tokens: 512 },
    }), () => {}, 3_000);
    assert.deepEqual(result.lines.at(-1), { type: "error", code: "TIMEOUT", message: "The runtime deadline was exceeded." });
  } finally {
    await new Promise<void>((resolve) => fake.server.close(() => resolve()));
  }
});
