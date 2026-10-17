"""Run the scheduled collector with a durable UTF-8 log."""

import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def execute(log_path: Path, command: str = 'run') -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('w', encoding='utf-8') as log:
        log.write(f'Started {datetime.now().astimezone().isoformat(timespec="seconds")}\n')
        log.flush()
        result = subprocess.run([sys.executable, '-m', 'collector', command],
                                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                check=False)
        log.write(f'\nExitCode: {result.returncode}\n')
        log.flush()
    return result.returncode


def main():
    stamp = datetime.now().astimezone().strftime('%Y-%m-%d_%H-%M-%S')
    log_path = ROOT / 'data' / 'logs' / f'{stamp}.log'
    raise SystemExit(execute(log_path))


if __name__ == '__main__':
    main()
