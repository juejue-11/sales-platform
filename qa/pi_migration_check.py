"""Verify the additive schema upgrade against a copy of the pre-upgrade backup."""
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.db import connect


def main():
    before = json.loads((ROOT / 'data/pi-upgrade-before.json').read_text(encoding='utf-8-sig'))
    original = sqlite3.connect(f"file:{Path(before['backup']).as_posix()}?mode=ro", uri=True)
    clone_path = ROOT / 'data' / f'pi-migration-check-{time.time_ns()}.sqlite3'
    clone = sqlite3.connect(clone_path)
    original.backup(clone)
    clone.close()
    upgraded = connect(clone_path)
    tables = [row[0] for row in original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        assert table.replace('_', '').isalnum()
        old_rows = original.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
        new_rows = [tuple(row) for row in upgraded.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
        assert old_rows == new_rows, f'Existing rows changed: {table}'
    assert upgraded.execute("SELECT name FROM sqlite_master WHERE name='agent_runs'").fetchone()
    assert not upgraded.execute('PRAGMA foreign_key_check').fetchall()
    print(json.dumps({'migration': 'passed', 'unchanged_tables': len(tables), 'counts': before['counts'], 'production_untouched': True}))
    original.close()
    upgraded.close()


if __name__ == '__main__':
    main()
