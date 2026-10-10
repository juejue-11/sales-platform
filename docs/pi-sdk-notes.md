# Embedded Pi SDK notes

This runtime uses the pinned Pi SDK packages in `agent-runtime/package.json`:

- `@earendil-works/pi-coding-agent@1.1.0`
- `@earendil-works/pi-agent-core@1.1.0`
- `@earendil-works/pi-ai@1.1.0`

The installed SDK package declares Node.js `>=22.19.0`; the package source at the
same release tag reports version `1.1.0` and the same engine requirement.
[Pi v1.1.0 package metadata](https://github.com/earendil-works/pi/blob/v1.1.0/packages/coding-agent/package.json#L1-L14)
[Pi v1.1.0 engine field](https://github.com/earendil-works/pi/blob/v1.1.0/packages/coding-agent/package.json#L99-L107)

## SDK facts used by this integration

The official SDK documentation describes `@earendil-works/pi-coding-agent` as an
in-process Node.js/Bun embedding API. It uses `createAgentSession`, resolves a
prompt with `session.prompt()`, and requires `session.dispose()` when the session
ends. [SDK overview](https://pi.dev/docs/latest/sdk#sdk)

The SDK supports an in-memory `SessionManager` for hosts that do not want session
files. [Session storage](https://pi.dev/docs/latest/sdk#session-storage)

The SDK factory accepts explicit model, model runtime, settings manager, resource
loader, tool allowlist, and custom tools. The official full-control example also
shows an empty `ResourceLoader` implementation and an in-memory settings manager.
[Session configuration](https://pi.dev/docs/latest/sdk#configuring-a-session)
[v1.1.0 full-control example](https://github.com/earendil-works/pi/blob/v1.1.0/packages/coding-agent/examples/sdk/12-full-control.ts)

The SDK's tools example documents selecting tools with the `tools` option. This
runtime sets `noTools: "all"`, supplies the three snapshot tools through
`customTools` for protocol 1 and adds only the host-bridged document tool for
protocol 2. It also supplies an explicit allowlist/denylist. That is stricter
than the example because this process must never expose Pi's default `read`,
`bash`, `edit`, `write`, `grep`, `find`, `ls`, or PowerShell tools.
[v1.1.0 tools example](https://github.com/earendil-works/pi/blob/v1.1.0/packages/coding-agent/examples/sdk/05-tools.ts)

The OpenAI-compatible provider path accepts a model with API type
`openai-completions` and a configured base URL. The runtime registers that model
directly in an in-memory `ModelRuntime` and uses the request's API key as the
provider's bearer credential. It does not read `auth.json`, `models.json`, Pi
settings, project `AGENTS.md`, home skills, extensions, or model catalogs.

## Version-specific implementation choices

The current online SDK page and the v1.1.0 source example use the same public
factory names (`createAgentSession`, `ModelRuntime`, `SessionManager`,
`SettingsManager`, and `ResourceLoader`). The v1.1.0 package source still allows
the simpler default factory, but that form intentionally discovers the working
directory and configured resources. This runtime uses the explicit full-control
form and pins all three first-party SDK packages to `1.1.0` so that model and
agent-core types cannot drift independently.

The v1.1.0 source example uses file paths for `authPath` and `modelsPath` and can
set a runtime API key. The screening bridge instead passes
`InMemoryCredentialStore`, `modelsPath: null`, `allowModelNetwork: false`, and
`refreshOnCreate: false`; this is the required host-owned auth/settings boundary.
[v1.1.0 full-control source](https://raw.githubusercontent.com/earendil-works/pi/v1.1.0/packages/coding-agent/examples/sdk/12-full-control.ts)

Pi's upstream README states that the CLI runs with the permissions of its process
and does not provide a built-in filesystem/process/network permission boundary.
This is why this integration supplies no default coding tools and exposes only
read-only closures over the request context. [Pi permissions note](https://github.com/earendil-works/pi#permissions--containerization)

## Host protocol and verification

`agent-runtime/dist/runner.js` reads one JSON request from stdin and writes JSONL
progress, result, or error events to stdout. Progress events never include tool
arguments, tool results, model deltas, or secrets. The result keeps the caller's
first system message as the SDK system prompt, including the platform's required
output schema. Protocol 1 tool calls are limited to project records, the company
capability card, and related-record candidates; protocol 2 adds the bounded
host-bridged public-document fetch. Related projects are explicitly labelled as
candidate relations and not proof of the same project. A failed business-tool
completion adds `is_error: true` to its `tool_end` progress event; successful
`tool_end` events omit that field. A result is emitted only when the final
assistant message has SDK stop reason `stop`; length, error, aborted, or tool-use
termination is returned as a safe model error. Assistant text is capped during
streaming and at finalization at an independent 256 KiB UTF-8 byte limit, and a
final response containing the exact configured API key is rejected before it can
be written to stdout.

The integration test runs a local fake OpenAI-compatible SSE server. It verifies a
tool request followed by execution and a second model response, bearer-key
selection over an ambient `OPENAI_API_KEY`, absence of default tools, redacted
model failures, and the turn limit before a second HTTP request.

Build and test from `agent-runtime` with the bundled runtime paths:

```powershell
$env:Path = "C:\Users\cgr\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;" + $env:Path
& "C:\Users\cgr\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd" install --ignore-scripts
& "C:\Users\cgr\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd" build
& "C:\Users\cgr\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd" test
```

The tests use only a local fake endpoint and never call a production model.

## On-demand public document bridge (protocol 2)

Protocol 1 remains the compatibility path: the runner receives the historical
EOF-delimited JSON request and exposes exactly the three snapshot tools
(`read_project_records`, `read_company_capability`, and `read_related_records`).
Protocol 2 is selected by setting `request.protocol` to `2` on the first JSON
line. The first line is framed, stdin stays open, and the runner can ask the
host for project documents while the model turn is running:

```json
{"type":"tool_request","id":"rpc-1","tool":"fetch_project_documents","arguments":{"record_id":7}}
```

The host answers on stdin with either a document snapshot or one of the fixed
safe error codes:

```json
{"type":"tool_response","id":"rpc-1","ok":true,"data":{"documents":[],"procurement":{}}}
{"type":"tool_response","id":"rpc-1","ok":false,"error":"not_found"}
```

`fetch_project_documents` is the fourth custom tool and is present only in
protocol 2. Its only argument is an optional integer `record_id`. The runner
accepts an ID only when it belongs to the current project's records; it never
accepts a URL, local path, request header, credential, or arbitrary network
selector from the model. The host owns source URL selection, HTTPS policy,
redirect policy, byte/time limits, PDF/HTML parsing, and persistence. The
runner bounds one RPC response line at 4 MiB, keeps the model's final response
bound at 256 KiB, and maps malformed, unknown, or premature-EOF bridge frames
to a redacted `RPC_ERROR`. A pending request is rejected on every bridge
failure so the child cannot wait indefinitely. Protocol 2 also retains the
request deadline for unanswered tool calls.

The protocol 2 system suffix requires a fetch before judging when the body
mentions an attachment/tender document or lacks procurement scope, specs, or
delivery facts. It requires an explicit evidence gap when fetching or parsing
fails, forbids invented values, and says that a bid deadline is not a delivery
date. The model-facing tool result is JSON containing `documents` and
`procurement`; each document may include `id`, `record_id`, `name`, `url`,
`status`, `pages`, `sha256`, `fetched_at`, `mime_type`, `byte_size`, `text`,
`page_texts`, and extracted `items`. The host should retain the complete
server-side snapshot and validate any final `record_id`/`document_id`/`quote`
evidence against the fetched text and hash before persistence. The runner
does not treat a URL or quote supplied by the model as proof.

The host bridge should allow only the documented error enum (`invalid_request`,
`record_not_in_project`, `not_configured`, `not_found`, `unsupported`,
`auth_required`, `failed`, `network_error`, `parse_failed`, `unreadable`,
`timeout`, `cancelled`, and `internal`). In particular, provider exception
text must never be sent as `error`; this prevents secrets, response bodies, and
arbitrary diagnostics from crossing the RPC boundary. On success the bridge
must return server-generated document IDs and source fields, not a model-chosen
ID. The Python host can therefore implement the fourth tool without opening a
second daemon: keep the child stdin/stdout pipes alive for protocol 2, dispatch
the single allowlisted request to its existing trusted fetch/parser, and close
the pipes on completion, cancellation, timeout, or malformed frames.

The protocol 2 tests use only a local fake model and deterministic host
responses. They cover successful trusted material injection, safe fetch
failure, record-ID isolation, unknown/invalid host responses, host EOF, and
an unanswered tool timeout. Existing protocol 1 tests continue to exercise the
EOF-delimited three-tool loop.
