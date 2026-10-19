import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { DUPLEX_PROTOCOL_VERSION, MAX_INPUT_BYTES } from "./constants.js";
import { parseRequest } from "./request.js";
import { DuplexRpc, JsonLineReader } from "./rpc.js";
import { normalizeError } from "./execution-support.js";
import { runRequest } from "./execution.js";
import { ProtocolError, isObject, type RuntimeEvent } from "./types.js";

export type {
  AssessSourceRelationArguments,
  FetchProjectDocumentsArguments,
  HostRpcArguments,
  HostRpcToolName,
  HostToolArguments,
  HostToolBridge,
  ReadResearchSourceArguments,
  ResearchContext,
  RuntimeConfig,
  RuntimeContext,
  RuntimeEvent,
  RuntimeLimits,
  RuntimeMessage,
  RuntimeRequest,
  SearchProjectSourcesArguments,
  SearchResearchContentArguments,
  SourceRelation,
  ToolName,
} from "./types.js";
export { DuplexRpc } from "./rpc.js";
export { runRequest } from "./execution.js";

function emitLine(event: unknown): void {
  process.stdout.write(`${JSON.stringify(event)}\n`);
}

async function readInitialRequest(): Promise<{ reader: JsonLineReader; parsed: unknown }> {
  const reader = new JsonLineReader(process.stdin);
  const firstLine = await reader.readLine(MAX_INPUT_BYTES);
  if (firstLine === null) throw new ProtocolError("invalid json");

  let firstParsed: unknown;
  try {
    firstParsed = JSON.parse(firstLine);
  } catch {
    firstParsed = undefined;
  }
  // Protocol 2 is explicitly framed: the initial request is one JSON line and
  // stdin remains open for tool_response frames. Protocol 1 keeps the legacy
  // EOF-delimited request path, including pretty-printed JSON.
  if (isObject(firstParsed) && firstParsed.protocol === DUPLEX_PROTOCOL_VERSION) {
    return { reader, parsed: firstParsed };
  }
  const firstBytes = Buffer.byteLength(firstLine, "utf8");
  const remaining = await reader.readRemaining(Math.max(0, MAX_INPUT_BYTES - firstBytes - 1));
  const raw = remaining ? `${firstLine}\n${remaining}` : firstLine;
  let parsed = firstParsed;
  if (parsed === undefined) {
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new ProtocolError("invalid json");
    }
  }
  if (isObject(parsed) && parsed.protocol === DUPLEX_PROTOCOL_VERSION) {
    throw new ProtocolError("protocol 2 request must be one JSON line");
  }
  return { reader, parsed };
}

export async function main(): Promise<void> {
  let reader: JsonLineReader | undefined;
  let rpc: DuplexRpc | undefined;
  try {
    const initial = await readInitialRequest();
    reader = initial.reader;
    const request = parseRequest(initial.parsed);
    if (request.protocol === DUPLEX_PROTOCOL_VERSION) {
      rpc = new DuplexRpc(reader, emitLine, Date.now() + request.limits.timeout_ms);
    }
    const result = await runRequest(request, emitLine as (event: RuntimeEvent) => void, rpc);
    emitLine(result);
  } catch (error) {
    emitLine(normalizeError(error));
  } finally {
    rpc?.close();
    reader?.close();
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  void main();
}
