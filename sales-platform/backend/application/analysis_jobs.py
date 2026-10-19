"""Analysis job execution and validated judgment persistence."""
from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

import asyncio
import copy
import inspect
import sqlite3
from ..analysis.errors import AIError, sanitize_ai_error
from ..analysis.providers import OpenAICompatibleProvider
from ..analysis.runtime_bridge import PiSDKProvider, PiSDKResult
from ..analysis.prompts import build_messages, correction_messages
from ..analysis.validation import extract_content, validate_judgment
from ..research.config import RESEARCH_POLICY_VERSION, RESEARCH_TOOLS, configuration_fingerprint
from ..research.session import ResearchSession
from ..analysis.provider_finalizer import deepseek_formatter_endpoint, strict_format_report
from ..core.utils import json_text, now_iso, sha256_text
from ..procurement import procurement_fingerprint
from .reading import ReadingProtocolMismatch
from .state import ApplicationState

if TYPE_CHECKING:
    from .agent_runs import AgentRuns
    from .analysis_context import AnalysisContext
    from .documents import ProjectDocuments
    from .projects import ProjectQueries
    from .reading_jobs import ReadingJobs
    from .reading_store import ProcurementReadings
    from .records import ProjectRecords
    from .settings import ApplicationSettings


class AnalysisJobs:
    def __init__(
        self, *,
        state: ApplicationState,
        agent_runs: AgentRuns,
        analysis_context: AnalysisContext,
        documents: ProjectDocuments,
        projects: ProjectQueries,
        reading_jobs: ReadingJobs,
        reading_store: ProcurementReadings,
        records: ProjectRecords,
        settings: ApplicationSettings,
        queue_project: Callable[..., Any],
    ) -> None:
        self.state = state
        self.agent_runs = agent_runs
        self.analysis_context = analysis_context
        self.documents = documents
        self.projects = projects
        self.reading_jobs = reading_jobs
        self.reading_store = reading_store
        self.records = records
        self.settings = settings
        self.queue_project = queue_project

    async def run_job(self, job_id: int) -> None:
        if job_id in self.state.active_jobs:
            return
        self.state.active_jobs.add(job_id)
        current_task = asyncio.current_task()
        if current_task is not None:
            self.state.run_tasks.add(current_task)
        agent_run_id: int | None = None
        research_session: ResearchSession | None = None
        research_configuration_snapshot: str | None = None
        legacy_judgment_content: str | None = None
        legacy_judgment_usage: dict[str, int] = {}
        try:
            with self.state.lock, self.state.db:
                job = self.state.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                if not job or job["status"] not in {"queued", "running"}:
                    return
                attempts = int(job["attempts"] or 0) + 1
                self.state.db.execute("UPDATE jobs SET status='running',attempts=?,started_at=?,updated_at=?,finished_at=NULL,error=NULL WHERE id=?",
                                (attempts, now_iso(), now_iso(), job_id))
            project_id = int(job["project_id"])
            project = self.projects.project_detail(project_id)
            config = self.settings.ai_config()
            if not config["configured"]:
                if (config.get("credentials_configured") and config.get("engine") == "pi_sdk"
                        and not config.get("sdk_ready") and isinstance(self.state.provider, PiSDKProvider)):
                    raise AIError("Pi SDK运行时未就绪，请安装Node.js 22.19+并构建agent-runtime/dist/runner.js")
                raise AIError("AI is not configured")
            # Procurement reading is an independent, source-grounded stage.
            # The legacy injected judgment doubles opt out unless they expose
            # read/read_procurement; real Pi SDK and single-call providers
            # always pass through the stage before a judgment is generated.
            explicit_reader = callable(getattr(self.state.provider, "read_procurement", None)) or callable(getattr(self.state.provider, "read", None))
            if (not self.state.injected_provider) or explicit_reader or bool(getattr(self.state.provider, "supports_procurement_reading", False)):
                # A failed reading remains an explicit, persisted input state.
                # The judgment may still use the primary announcement as a
                # fallback, but its prompt carries the failed/partial reading
                # and unresolved evidence so it cannot present that stage as
                # successfully completed.
                try:
                    reading_result = await self.reading_jobs.run_reading(project_id, force=False)
                except ReadingProtocolMismatch as mismatch:
                    # Older Pi runners do not echo task_kind. Their first
                    # response is the legacy judgment draft; reuse it instead
                    # of spending a second model turn before the judgment.
                    legacy_judgment_content = mismatch.content
                    legacy_judgment_usage = mismatch.usage
                else:
                    reading_status = str(reading_result.get("status") or "failed")
                    if reading_status in {"failed", "pending", "not_configured"}:
                        raise AIError("采购预读未完成：" + str(reading_result.get("error") or reading_status))
                project = self.projects.project_detail(project_id)
            gate, _ = self.reading_store.gate(self.records.records_for_project(project_id), project_id)
            if gate in {"excluded", "expired", "duplicate", "closed"}:
                with self.state.lock, self.state.db:
                    self.state.db.execute("UPDATE jobs SET status='skipped',error=?,finished_at=?,updated_at=? WHERE id=?",
                                    (f"gate:{gate}", now_iso(), now_iso(), job_id))
                return
            capability = self.settings.section("business")
            records = self.records.records_for_project(project_id)
            context, related_context = self.analysis_context.analysis_context(project_id, capability, records)
            # Keep a full, bounded-by-ID validation snapshot separate from the
            # SDK context. It is captured before awaiting the provider so a
            # concurrent import cannot mix a new body with the old fingerprint.
            related_records = self.analysis_context.analysis_related_validation_records(related_context)
            document_sources = self.records.document_sources_for_records(
                [int(record["id"]) for record in records if record.get("id") is not None]
            )
            research_configuration_snapshot = configuration_fingerprint(self.state.research_sources_path)
            fingerprint = self.analysis_context.analysis_fingerprint(project, capability, config, related_context,
                                                     research_configuration=research_configuration_snapshot)
            with self.state.lock, self.state.db:
                self.state.db.execute("UPDATE jobs SET input_fingerprint=?,updated_at=? WHERE id=?", (fingerprint, now_iso(), job_id))
            messages = build_messages(project, capability, related_context)
            provider: Any = self.state.provider
            if config.get("engine") == "single_call" and isinstance(provider, PiSDKProvider) and not self.state.injected_provider:
                provider = OpenAICompatibleProvider()
            uses_agent = config.get("engine") == "pi_sdk" and callable(getattr(provider, "analyze", None))
            if uses_agent:
                document_sources = []
                document_cache: dict[int, list[dict[str, Any]]] = {}
                agent_run_id = self.agent_runs.new_agent_run(job_id, project_id)
                research_session = ResearchSession(
                    self.state.db, project_id=project_id, job_id=job_id, records=records,
                    db_lock=self.state.lock, sources_path=self.state.research_sources_path, client=self.state.research_client,
                )
                self.agent_runs.record_investigation(agent_run_id, research_session, configuration=research_configuration_snapshot)
                fingerprint_project = copy.deepcopy(project)

                async def on_event(event: dict[str, Any]) -> None:
                    if event.get("stage") == "usage":
                        self.agent_runs.update_agent_usage(agent_run_id, event.get("usage"))
                    else:
                        self.agent_runs.record_agent_event(agent_run_id, event)

                async def tool_callback(arguments: dict[str, Any], *, tool: str = "fetch_project_documents") -> dict[str, Any]:
                    nonlocal fingerprint, document_sources
                    if str(config.get("api_key") or "") and config["api_key"] in json_text(arguments):
                        raise AIError("工具参数包含受限凭据")
                    if tool in RESEARCH_TOOLS:
                        try:
                            return await research_session.execute(tool, arguments)
                        finally:
                            self.agent_runs.record_investigation(agent_run_id, research_session,
                                                       configuration=research_configuration_snapshot)
                    if tool != "fetch_project_documents":
                        raise AIError("工具参数无效")
                    value = await self.documents.agent_fetch_documents(project_id, arguments, cache=document_cache, records=records)
                    requested_record_id = arguments.get("record_id") if isinstance(arguments, dict) else None
                    target_record_ids = ({int(record["id"]) for record in records}
                                         if requested_record_id is None else {int(requested_record_id)})
                    document_sources = self.analysis_context.merge_job_document_reads(
                        document_sources, value.get("documents") if isinstance(value, dict) else None,
                        target_record_ids,
                    )
                    # The tool response is part of this job's trusted input.
                    # Keep the original source/capability/config snapshot and
                    # replace only the procurement component. A concurrent
                    # source import is still detected by the final comparison.
                    if isinstance(value, dict) and isinstance(value.get("procurement"), dict):
                        procurement_snapshot = self.records.procurement_for_project(project_id)
                        fingerprint_project["procurement"] = procurement_snapshot
                        fingerprint_project["procurement_fingerprint"] = procurement_fingerprint(procurement_snapshot)
                        # Attachment reads also change the source package
                        # version exposed to the shared reading contract.
                        # Keep the in-job snapshot aligned so the document
                        # fetch remains part of this job rather than looking
                        # like a concurrent change that must be requeued.
                        reading_snapshot = self.reading_store.procurement_reading_for_project(project_id)
                        fingerprint_project["procurement_reading"] = reading_snapshot
                        fingerprint_project["procurement_reading_fingerprint"] = sha256_text(
                            json_text(reading_snapshot)
                        )
                    fingerprint = self.analysis_context.analysis_fingerprint(
                        fingerprint_project, capability, config, related_context,
                        research_configuration=research_configuration_snapshot,
                    )
                    return value

            if legacy_judgment_content is not None:
                if agent_run_id is not None:
                    self.agent_runs.update_agent_usage(agent_run_id, legacy_judgment_usage)
            runtime_limits = self.agent_runs.agent_limits()
            uses_formatter = (uses_agent and isinstance(provider, PiSDKProvider) and not self.state.injected_provider
                              and deepseek_formatter_endpoint(str(config.get("base_url") or "")) is not None)

            async def finalize_draft(draft: str, error: AIError, evidence: dict[str, Any]) -> str:
                usage_recorded = False

                def record_usage(value: dict[str, int]) -> None:
                    nonlocal usage_recorded
                    usage_recorded = True
                    if agent_run_id is not None:
                        self.agent_runs.update_agent_usage(agent_run_id, value)

                result = await strict_format_report(
                    base_url=str(config.get("base_url") or ""),
                    model=str(config.get("model") or ""),
                    api_key=str(config.get("api_key") or ""),
                    messages=[*messages, {"role": "user", "content": json_text({"validation_error": str(error)})}],
                    previous_output=draft,
                    available_evidence=evidence,
                    max_output_tokens=int(runtime_limits.get("max_output_tokens", 16_384)),
                    on_usage=record_usage,
                )
                if agent_run_id is not None and not usage_recorded:
                    self.agent_runs.update_agent_usage(agent_run_id, result.usage)
                return result.content

            for correction in range(2):
                if correction:
                    with self.state.lock, self.state.db:
                        self.state.db.execute("UPDATE jobs SET attempts=attempts+1,updated_at=? WHERE id=?",
                                        (now_iso(), job_id))
                # Request failures (including credentials, timeouts and rate limits)
                # are not output errors and must not trigger a correction request.
                if correction and uses_formatter:
                    # Keep semantic correction on the same tool-free YAML path;
                    # restarting the JSON agent can reintroduce malformed output.
                    content = await finalize_draft(content, correction_error, available_evidence)
                elif legacy_judgment_content is not None and correction == 0:
                    content = legacy_judgment_content
                elif uses_agent:
                    runtime_limits = self.agent_runs.agent_limits()
                    if correction:
                        # Correct only the draft against this job's read evidence.
                        # A new investigation cannot repair a serialization typo.
                        runtime_limits = {**runtime_limits, "max_turns": 1, "max_tool_calls": 0}
                    analyze_kwargs = {
                        "base_url": config["base_url"], "model": config["model"], "api_key": config["api_key"],
                        "messages": messages, "context": context, "limits": runtime_limits, "on_event": on_event,
                    }
                    try:
                        parameters = inspect.signature(provider.analyze).parameters  # type: ignore[attr-defined]
                    except (TypeError, ValueError):
                        parameters = {}
                    if "tool_callback" in parameters or any(parameter.kind == inspect.Parameter.VAR_KEYWORD
                                                            for parameter in parameters.values()):
                        analyze_kwargs["tool_callback"] = tool_callback
                    result = await provider.analyze(**analyze_kwargs)  # type: ignore[attr-defined]
                    if isinstance(result, PiSDKResult):
                        content = result.content
                        usage = result.usage
                    elif isinstance(result, dict) and isinstance(result.get("content"), str):
                        content = result["content"]
                        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
                    else:
                        raise AIError("Pi SDK结果协议无效")
                    if agent_run_id is not None:
                        self.agent_runs.update_agent_usage(agent_run_id, usage)
                else:
                    response = await provider.complete(base_url=config["base_url"], model=config["model"],
                                                            api_key=config["api_key"], messages=messages)
                    content = extract_content(response)
                try:
                    judgment = validate_judgment(content, project, config["model"], fingerprint,
                                                 related_records=related_records,
                                                 forbidden_values=[str(config.get("api_key") or "")],
                                                 document_sources=document_sources,
                                                 company_material=capability,
                                                 research_sources=research_session.evidence_sources() if research_session else [],
                                                 require_report=isinstance(provider, (PiSDKProvider, OpenAICompatibleProvider)))
                    break
                except AIError as exc:
                    # The single semantic correction is terminal, even if its
                    # output fails formatting. Do not add another repair pass.
                    if correction:
                        raise
                    available_evidence = {
                        "documents": [{key: document.get(key) for key in
                                       ("id", "record_id", "name", "read_fragments")}
                                      for document in document_sources],
                        "research_sources": research_session.evidence_sources() if research_session else [],
                    }
                    # A malformed final report from the official DeepSeek
                    # provider gets one isolated YAML serialization pass. This
                    # avoids replaying the agent investigation and cannot
                    # execute any business tool; the decoded JSON is still
                    # checked by validate_judgment below.
                    if uses_formatter and exc.code == "MODEL_FORMAT_ERROR":
                        with self.state.lock, self.state.db:
                            self.state.db.execute("UPDATE jobs SET attempts=attempts+1,updated_at=? WHERE id=?",
                                            (now_iso(), job_id))
                        if agent_run_id is not None:
                            self.agent_runs.record_agent_event(agent_run_id, {"stage": "model"})
                        content = await finalize_draft(content, exc, available_evidence)
                        try:
                            judgment = validate_judgment(
                                content, project, config["model"], fingerprint,
                                related_records=related_records,
                                forbidden_values=[str(config.get("api_key") or "")],
                                document_sources=document_sources,
                                company_material=capability,
                                research_sources=research_session.evidence_sources() if research_session else [],
                                require_report=True,
                            )
                        except AIError as finalizer_error:
                            # A successful format conversion may still fail
                            # trusted evidence or report semantics.  On the
                            # first pass, feed that validation error into the
                            # existing single semantic correction.  Formatter
                            # failures and second-pass failures remain terminal.
                            if finalizer_error.code == "MODEL_FORMAT_ERROR":
                                raise
                            exc = finalizer_error
                        else:
                            break
                    if agent_run_id is not None:
                        # Retain bounded correction progress in the public
                        # activity feed.  The raw validation error can contain
                        # model text, so only use the trusted stage label.
                        self.agent_runs.record_agent_event(agent_run_id, {"stage": "model"})
                    if uses_formatter:
                        correction_error = exc
                    else:
                        messages = correction_messages(messages, exc, previous_output=content,
                                                       available_evidence=available_evidence)
            judgment["created_at"] = now_iso()
            dependencies = self.agent_runs.cited_research_dependencies(judgment, research_session)
            if uses_agent:
                # Only cited versions affect the final report identity. Search
                # candidates and unused reads cannot invalidate the job itself.
                fingerprint = self.analysis_context.analysis_fingerprint(fingerprint_project, capability, config,
                                                         related_context, dependencies, research_configuration_snapshot)
            judgment["input_fingerprint"] = fingerprint
            if agent_run_id is not None and research_session is not None:
                investigation = research_session.investigation()
                has_events = bool(investigation.get("events"))
                status = "limited" if investigation.get("status") == "limited" else (
                    "completed" if has_events else "not_performed")
                self.agent_runs.record_investigation(agent_run_id, research_session, status=status,
                                           stop_reason=investigation.get("stop_reason") or (
                                               "补查已结束，未解决事项见下方" if has_events else "本轮依据已有资料分析，未执行主动补查"),
                                           unresolved=judgment["unknowns"], configuration=research_configuration_snapshot)
            # A file import or settings update may have completed while the
            # provider was awaiting the network. Preserve this result only as a
            # stale audit entry, then enqueue a fresh generation from current
            # inputs. It must never become the current recommendation.
            current_project = self.projects.project_detail(project_id)
            current_config = self.settings.ai_config()
            current_capability = self.settings.section("business")
            current_context, current_related_context = self.analysis_context.analysis_context(project_id, current_capability)
            stale_generation = fingerprint != self.analysis_context.analysis_fingerprint(
                current_project, current_capability, current_config, current_related_context,
                dependencies) or self.agent_runs.research_dependencies_changed(project_id, dependencies)
            with self.state.lock, self.state.db:
                self.state.db.execute("""INSERT INTO judgments(project_id,created_at,model,input_fingerprint,stale,decision,summary,demand_summary,
                                  reasons_json,unknowns_json,next_actions_json,options_json,analysis_meta_json,report_json,agent_run_id,
                                  research_dependencies_json,research_policy_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (project_id, judgment["created_at"], judgment["model"], judgment["input_fingerprint"], int(stale_generation), judgment["decision"],
                                 judgment["summary"], judgment["demand_summary"], json_text(judgment["reasons"]), json_text(judgment["unknowns"]),
                                 json_text(judgment["next_actions"]), json_text(judgment["options"]),
                                 json_text(judgment["analysis_meta"]),
                                 json_text(judgment["report"]) if isinstance(judgment.get("report"), dict) else None,
                                 agent_run_id, json_text(dependencies), RESEARCH_POLICY_VERSION))
                self.state.db.execute("UPDATE jobs SET status='succeeded',finished_at=?,updated_at=?,error=NULL WHERE id=?",
                                (now_iso(), now_iso(), job_id))
            if agent_run_id is not None:
                # Only this trusted event is persisted as completed.  Runner
                # completed notifications are ignored before validation.
                self.agent_runs.record_agent_event(agent_run_id, {"stage": "completed"})
                self.agent_runs.finish_agent_run(agent_run_id, "succeeded")
            if stale_generation and current_config["configured"]:
                self.queue_project(project_id, force=True, schedule=False)
        except asyncio.CancelledError:
            if agent_run_id is not None:
                if research_session is not None:
                    self.agent_runs.record_investigation(agent_run_id, research_session, status="limited", stop_reason="任务已取消",
                                               configuration=research_configuration_snapshot)
                self.agent_runs.finish_agent_run(agent_run_id, "failed", "任务已取消")
            if not self.state.closed:
                try:
                    with self.state.lock, self.state.db:
                        self.state.db.execute("UPDATE jobs SET status='failed',error=?,finished_at=?,updated_at=? WHERE id=?",
                                        ("任务已取消", now_iso(), now_iso(), job_id))
                except sqlite3.ProgrammingError:
                    pass
            raise
        except Exception as exc:
            # Do not retain provider response or request data in the database.
            error = sanitize_ai_error(exc, secrets=[str(config.get("api_key") or "")] if isinstance(locals().get("config"), dict) else [])
            if agent_run_id is not None:
                if research_session is not None:
                    self.agent_runs.record_investigation(agent_run_id, research_session, status="failed", stop_reason=error,
                                               configuration=research_configuration_snapshot)
                self.agent_runs.finish_agent_run(agent_run_id, "failed", error)
            try:
                with self.state.lock, self.state.db:
                    self.state.db.execute("UPDATE jobs SET status='failed',error=?,finished_at=?,updated_at=? WHERE id=?",
                                    (error[:500], now_iso(), now_iso(), job_id))
            except sqlite3.ProgrammingError:
                pass
        finally:
            try:
                if research_session is not None:
                    await research_session.close()
            finally:
                self.state.active_jobs.discard(job_id)
                if current_task is not None:
                    self.state.run_tasks.discard(current_task)
