import assert from "node:assert/strict";
import { once } from "node:events";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { spawn } from "node:child_process";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export interface CapturedRequest {
  body: Record<string, unknown>;
  headers: IncomingMessage["headers"];
  url: string | undefined;
}

export type Responder = (body: Record<string, unknown>, response: ServerResponse) => void;

export const here = dirname(fileURLToPath(import.meta.url));
export const runnerPath = join(here, "runner.js");
// Includes a fresh Pi SDK import in each child process on Windows. Production
// request deadlines are asserted separately inside the runner tests.
export const DUPLEX_HARNESS_TIMEOUT_MS = 15_000;
export const DUPLEX_KILL_GRACE_MS = 1_000;

export function assistantToolCallChunk(): Record<string, unknown> {
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

export function assistantCompanyToolCallChunk(topic?: string): Record<string, unknown> {
  return {
    id: "chatcmpl-test-company-tool",
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
              id: "call-company-capability",
              type: "function",
              function: { name: "read_company_capability", arguments: JSON.stringify(topic ? { topic } : {}) },
            },
          ],
        },
        finish_reason: "tool_calls",
      },
    ],
  };
}

export function assistantMalformedFetchToolCallChunk(): Record<string, unknown> {
  return {
    id: "chatcmpl-malformed-fetch",
    object: "chat.completion.chunk",
    created: 1,
    model: "screening-test-model",
    choices: [{
      index: 0,
      delta: {
        role: "assistant",
        tool_calls: [{
          index: 0,
          id: "call-malformed-fetch",
          type: "function",
          function: { name: "fetch_project_documents", arguments: '{"record_id":7' },
        }],
      },
      finish_reason: "tool_calls",
    }],
  };
}

export function interleavedRpcToolCallChunks(): Record<string, unknown>[] {
  return [
    {
      id: "chatcmpl-interleaved-tools",
      object: "chat.completion.chunk",
      created: 1,
      model: "screening-test-model",
      choices: [{
        index: 0,
        delta: {
          role: "assistant",
          tool_calls: [
            {
              index: 0,
              id: "call-fetch-interleaved",
              type: "function",
              function: { name: "fetch_project_documents", arguments: '{"record_id":' },
            },
            {
              index: 1,
              id: "call-search-interleaved",
              type: "function",
              function: { name: "search_project_sources", arguments: '{"purpose":"check","query":"equip' },
            },
          ],
        },
        finish_reason: null,
      }],
    },
    {
      id: "chatcmpl-interleaved-tools",
      object: "chat.completion.chunk",
      created: 1,
      model: "screening-test-model",
      choices: [{
        index: 0,
        delta: {
          // The final fragments arrive in the opposite order. The closing
          // brace and finish reason share this provider chunk.
          tool_calls: [
            { index: 1, function: { arguments: 'ment","limit":1}' } },
            { index: 0, function: { arguments: "7}" } },
          ],
        },
        finish_reason: "tool_calls",
      }],
    },
    {
      id: "chatcmpl-interleaved-tools",
      object: "chat.completion.chunk",
      created: 1,
      model: "screening-test-model",
      choices: [],
      usage: { prompt_tokens: 12, completion_tokens: 8, total_tokens: 20 },
    },
  ];
}

export function streamResponse(response: ServerResponse, chunks: Record<string, unknown>[]): void {
  response.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    connection: "keep-alive",
  });
  for (const chunk of chunks) response.write(`data: ${JSON.stringify(chunk)}\n\n`);
  response.write("data: [DONE]\n\n");
  response.end();
}

export async function startFakeServer(responder: Responder): Promise<{
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

export async function runRunner(input: Record<string, unknown>): Promise<{ lines: Record<string, unknown>[]; stderr: string }> {
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

export async function runDuplexRunner(
  input: Record<string, unknown>,
  onToolRequest: (frame: Record<string, unknown>, child: ReturnType<typeof spawn>) => void,
  timeoutMs = DUPLEX_HARNESS_TIMEOUT_MS,
  initialHostFrames: Record<string, unknown>[] = [],
  closeInputAfterInitial = false,
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
  for (const frame of initialHostFrames) child.stdin.write(`${JSON.stringify(frame)}\n`);
  if (closeInputAfterInitial) child.stdin.end();
  return await new Promise((resolve, reject) => {
    let settled = false;
    let timedOut = false;
    let killGraceTimer: ReturnType<typeof setTimeout> | undefined;
    const timeoutError = () => new Error(`duplex runner timed out after ${timeoutMs}ms`);
    const cleanup = () => {
      clearTimeout(timer);
      if (killGraceTimer) clearTimeout(killGraceTimer);
      child.removeListener("error", onError);
      child.removeListener("close", onClose);
    };
    const rejectOnce = (error: Error) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    };
    const onError = (error: Error) => {
      if (timedOut) return;
      rejectOnce(error);
    };
    const onClose = (code: number | null) => {
      if (timedOut) {
        rejectOnce(timeoutError());
        return;
      }
      if (settled) return;
      try {
        assert.equal(code, 0, `runner exited with ${code}; stderr=${Buffer.concat(stderr).toString("utf8")}`);
        if (pending.trim()) lines.push(JSON.parse(pending) as Record<string, unknown>);
        settled = true;
        cleanup();
        resolve({ lines, stderr: Buffer.concat(stderr).toString("utf8") });
      } catch (error) {
        rejectOnce(error instanceof Error ? error : new Error(String(error)));
      }
    };
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill();
      killGraceTimer = setTimeout(() => rejectOnce(timeoutError()), DUPLEX_KILL_GRACE_MS);
    }, timeoutMs);
    child.once("error", onError);
    child.once("close", onClose);
  });
}

export function requestFor(baseUrl: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
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

export function duplexRequestFor(baseUrl: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
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

export function assistantFetchToolCallChunk(
  recordId: number,
  argumentsValue: Record<string, unknown> = { record_id: recordId },
): Record<string, unknown> {
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
              function: { name: "fetch_project_documents", arguments: JSON.stringify(argumentsValue) },
            },
          ],
        },
        finish_reason: "tool_calls",
      },
    ],
  };
}

export function assistantResearchToolCallChunk(
  tool: string,
  id: string,
  argumentsValue: Record<string, unknown>,
): Record<string, unknown> {
  return {
    id: `chatcmpl-${id}`,
    object: "chat.completion.chunk",
    created: 1,
    model: "screening-test-model",
    choices: [
      {
        index: 0,
        delta: {
          role: "assistant",
          tool_calls: [{ index: 0, id, type: "function", function: { name: tool, arguments: JSON.stringify(argumentsValue) } }],
        },
        finish_reason: "tool_calls",
      },
    ],
  };
}
