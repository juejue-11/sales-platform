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
runtime sets `noTools: "all"`, supplies only the three business tools through
`customTools`, and also supplies an explicit allowlist/denylist. That is stricter
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
output schema. Tool calls are limited to project records, the company capability
card, and related-record candidates; related projects are explicitly labelled as
candidate relations and not proof of the same project. A failed business-tool
completion adds `is_error: true` to its `tool_end` progress event; successful
`tool_end` events omit that field.

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
