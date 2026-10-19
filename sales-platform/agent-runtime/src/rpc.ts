import { MAX_RPC_RESPONSE_BYTES, RPC_ERROR_CODES, RPC_TOOL_NAMES } from "./constants.js";
import {
  LimitError,
  ProtocolError,
  RpcError,
  isObject,
  type HostRpcArguments,
  type HostRpcToolName,
  type HostToolBridge,
  type JsonObject,
} from "./types.js";

export interface ByteStream extends AsyncIterable<Buffer | string> {
  pause?: () => void;
  destroy?: () => void;
}

/** Read newline-delimited frames while protocol 2 keeps stdin open. */
export class JsonLineReader {
  private readonly iterator: AsyncIterator<Buffer | string>;
  private buffer = Buffer.alloc(0);
  private ended = false;
  private closed = false;

  constructor(private readonly stream: ByteStream) {
    this.iterator = stream[Symbol.asyncIterator]();
  }

  async readLine(maxBytes: number): Promise<string | null> {
    while (true) {
      const newline = this.buffer.indexOf(0x0a);
      if (newline >= 0) {
        const line = this.buffer.subarray(0, newline);
        this.buffer = this.buffer.subarray(newline + 1);
        if (line.length > maxBytes) throw new ProtocolError("input too large");
        return line.toString("utf8").replace(/\r$/u, "");
      }
      if (this.buffer.length > maxBytes) throw new ProtocolError("input too large");
      if (this.ended || this.closed) {
        if (this.buffer.length === 0) return null;
        const line = this.buffer;
        this.buffer = Buffer.alloc(0);
        if (line.length > maxBytes) throw new ProtocolError("input too large");
        return line.toString("utf8").replace(/\r$/u, "");
      }
      const next = await this.iterator.next();
      if (next.done) {
        this.ended = true;
        continue;
      }
      const chunk = Buffer.isBuffer(next.value) ? next.value : Buffer.from(String(next.value));
      if (chunk.length > 0) this.buffer = Buffer.concat([this.buffer, chunk]);
    }
  }

  async readRemaining(maxBytes: number): Promise<string> {
    const chunks: Buffer[] = [];
    let total = this.buffer.length;
    if (this.buffer.length > 0) {
      chunks.push(this.buffer);
      this.buffer = Buffer.alloc(0);
    }
    while (!this.ended && !this.closed) {
      const next = await this.iterator.next();
      if (next.done) {
        this.ended = true;
        break;
      }
      const chunk = Buffer.isBuffer(next.value) ? next.value : Buffer.from(String(next.value));
      total += chunk.length;
      if (total > maxBytes) throw new ProtocolError("input too large");
      if (chunk.length > 0) chunks.push(chunk);
    }
    if (total > maxBytes) throw new ProtocolError("input too large");
    return Buffer.concat(chunks).toString("utf8");
  }

  close(): void {
    this.closed = true;
    this.buffer = Buffer.alloc(0);
    this.stream.pause?.();
    this.stream.destroy?.();
  }
}

interface PendingRpc {
  tool: HostRpcToolName;
  resolve: (value: JsonObject) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

function normalizedRpcStatus(error: string): string {
  const normalized = error.toLowerCase();
  if (normalized === "unsupported" || normalized === "not_configured" || normalized === "tool_unavailable") return "tool_unavailable";
  if (normalized === "auth_required" || normalized === "tool_forbidden") return "tool_forbidden";
  if (normalized === "invalid_request" || normalized === "tool_invalid") return "tool_invalid";
  if (normalized === "research_limit") return "research_limit";
  if (normalized === "source_not_found" || normalized === "not_found") return "source_not_found";
  if (normalized === "source_unreadable" || normalized === "unreadable" || normalized === "parse_failed") return "source_unreadable";
  if (normalized === "relation_unconfirmed") return "relation_unconfirmed";
  return "tool_error";
}

function rpcFailureResult(tool: HostRpcToolName, error: string): JsonObject {
  if (tool === "fetch_project_documents") return { documents: [], procurement: {}, error: error.toLowerCase() };
  return { status: normalizedRpcStatus(error), error: error.toLowerCase() };
}

export class DuplexRpc implements HostToolBridge {
  private nextId = 0;
  private closed = false;
  private terminalError: RpcError | undefined;
  private failureNotified = false;
  private onFailure: ((error: RpcError) => void) | undefined;
  private readonly pending = new Map<string, PendingRpc>();
  private readonly readerTask: Promise<void>;

  constructor(
    private readonly reader: JsonLineReader,
    private readonly writeLine: (value: unknown) => void,
    private readonly deadline: number,
  ) {
    this.readerTask = this.consumeResponses();
    void this.readerTask.catch(() => {});
  }

  request<Tool extends HostRpcToolName>(tool: Tool, argumentsValue: HostRpcArguments[Tool]): Promise<JsonObject> {
    if (this.terminalError) return Promise.reject(this.terminalError);
    if (this.closed) return Promise.reject(new RpcError("RPC bridge is closed"));
    if (!RPC_TOOL_NAMES.has(tool)) return Promise.reject(new RpcError("unsupported RPC tool"));
    const id = `rpc-${++this.nextId}`;
    const remaining = this.deadline - Date.now();
    if (remaining <= 0) return Promise.reject(new LimitError("timeout"));
    return new Promise<JsonObject>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new LimitError("timeout"));
      }, remaining);
      this.pending.set(id, { tool, resolve, reject, timer });
      try {
        this.writeLine({ type: "tool_request", id, tool, arguments: argumentsValue });
      } catch {
        this.pending.delete(id);
        clearTimeout(timer);
        reject(new RpcError("RPC bridge is not writable"));
      }
    });
  }

  setAbort(onFailure: (error: RpcError) => void): void {
    this.onFailure = onFailure;
    this.notifyFailure();
  }

  private notifyFailure(): void {
    if (this.failureNotified || !this.onFailure) return;
    const failure = this.terminalError ?? (this.closed ? new RpcError("RPC bridge closed") : undefined);
    if (!failure) return;
    this.failureNotified = true;
    this.onFailure(failure);
  }

  private async consumeResponses(): Promise<void> {
    try {
      while (!this.closed) {
        const line = await this.reader.readLine(MAX_RPC_RESPONSE_BYTES);
        if (line === null) throw new RpcError("RPC bridge closed");
        if (!line.trim()) continue;
        let parsed: unknown;
        try {
          parsed = JSON.parse(line);
        } catch {
          throw new RpcError("invalid RPC response");
        }
        if (!isObject(parsed) || parsed.type !== "tool_response" || typeof parsed.id !== "string") {
          throw new RpcError("unexpected RPC response");
        }
        const pending = this.pending.get(parsed.id);
        if (!pending) throw new RpcError("unknown RPC response id");
        if (parsed.ok === true) {
          if (!isObject(parsed.data)) throw new RpcError("invalid RPC response data");
          this.pending.delete(parsed.id);
          clearTimeout(pending.timer);
          pending.resolve(parsed.data);
          continue;
        }
        if (parsed.ok !== false || typeof parsed.error !== "string" || !RPC_ERROR_CODES.has(parsed.error)) {
          throw new RpcError("invalid RPC response error");
        }
        this.pending.delete(parsed.id);
        clearTimeout(pending.timer);
        pending.resolve(rpcFailureResult(pending.tool, parsed.error));
      }
    } catch (error) {
      const failure = error instanceof RpcError ? error : new RpcError();
      this.terminalError ??= failure;
      for (const [id, pending] of this.pending) {
        this.pending.delete(id);
        clearTimeout(pending.timer);
        pending.reject(failure);
      }
      if (!this.closed) {
        this.notifyFailure();
        throw failure;
      }
    }
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.terminalError ??= new RpcError("RPC bridge closed");
    for (const [id, pending] of this.pending) {
      this.pending.delete(id);
      clearTimeout(pending.timer);
      pending.reject(new RpcError("RPC bridge closed"));
    }
    this.reader.close();
  }
}
