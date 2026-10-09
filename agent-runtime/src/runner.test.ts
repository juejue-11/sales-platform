import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

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
