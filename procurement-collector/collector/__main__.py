"""Legacy collector CLI.

The command names remain compatible with the original standalone collector,
while every command that opens the writable SQLite database is protected by
the same process lock used by ``collector.control``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .db import connect, sync_sources
from .export import export_jsonl
from .locking import BusyError, locked
from .pipeline import reprocess_saved, run
from .report import save_report, trial_status


ROOT = Path(__file__).resolve().parent.parent


def _config(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"unable to read config: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise SystemExit("config must contain a sources list")
    ids = []
    for source in payload["sources"]:
        if not isinstance(source, dict) or not isinstance(source.get("id"), str) or not source["id"]:
            raise SystemExit("each source must have a non-empty string id")
        ids.append(source["id"])
    if len(ids) != len(set(ids)):
        raise SystemExit("config contains duplicate source ids")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="销售平台信息采集系统")
    parser.add_argument("--config", type=Path, default=ROOT / "sources.json")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("run", help="执行已启用来源并生成日报")
    collect.add_argument("--source", action="append", help="只运行指定来源 ID，可重复")
    report = commands.add_parser("report", help="重新生成指定日期日报")
    report.add_argument("--date", help="YYYY-MM-DD；默认今天（上海时间）")
    export = commands.add_parser("export", help="导出结构化底层记录")
    export.add_argument("--output", type=Path, help="JSONL 输出路径")
    commands.add_parser("status", help="显示来源覆盖和最近运行状态")
    commands.add_parser("trial", help="更新 14 天连续试运行状态")
    commands.add_parser("reprocess", help="从已存原文重新运行规则抽取")
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = _parser()
    args = parser.parse_args(argv)
    config = _config(args.config)
    if args.command == "run":
        configured_ids = {source["id"] for source in config["sources"]}
        requested_ids = set(args.source or [])
        unknown = sorted(requested_ids - configured_ids)
        # Reject before acquiring/creating a database.  This is particularly
        # useful for a typo in a hand-run command and keeps the old exit 2
        # contract for argument errors.
        if unknown:
            parser.error(f"unknown source: {', '.join(unknown)}")

    try:
        with locked(args.data):
            # connect() creates the schema, so it must stay inside the lock.
            db = connect(args.data / "collector.sqlite3")
            try:
                sync_sources(db, config["sources"])
                if args.command == "run":
                    export_path = args.data / "export" / "records.jsonl"

                    def publish_current():
                        return export_jsonl(db, export_path)

                    # Make the last committed snapshot available before this
                    # run starts; an empty database therefore publishes an
                    # empty JSONL file as well.
                    exported = publish_current()
                    run_id, stats = run(db, config, args.data, only=set(args.source or []),
                                        on_record=publish_current)
                    output = save_report(db, args.data)
                    trial_status(db, args.data)
                    exported = publish_current()
                    print(json.dumps({
                        "run_id": run_id,
                        "stats": stats,
                        "report": str(output),
                        "exported_records": exported,
                    }, ensure_ascii=False))
                    if stats["source_failed"]:
                        raise SystemExit(2)
                elif args.command == "report":
                    output = save_report(db, args.data, args.date)
                    print(output)
                elif args.command == "export":
                    path = args.output or args.data / "export" / "records.jsonl"
                    count = export_jsonl(db, path)
                    print(json.dumps({"records": count, "path": str(path)}, ensure_ascii=False))
                elif args.command == "trial":
                    print(trial_status(db, args.data))
                elif args.command == "reprocess":
                    count = reprocess_saved(db)
                    output = save_report(db, args.data)
                    export_jsonl(db, args.data / "export" / "records.jsonl")
                    print(json.dumps({"reprocessed": count, "report": str(output)}, ensure_ascii=False))
                else:
                    for row in db.execute("SELECT * FROM sources ORDER BY id"):
                        print(f"{row['id']} | enabled={bool(row['enabled'])} | {row['access_status']} | "
                              f"last_success={row['last_success_at'] or '-'} | "
                              f"last_error={row['last_error'] or '-'}")
            finally:
                db.close()
    except BusyError as exc:
        print(json.dumps({"error": str(exc), "code": "busy"}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(3) from exc


if __name__ == "__main__":
    main()
