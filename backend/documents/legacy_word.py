"""Bounded, unattended legacy Word conversion using an isolated LibreOffice profile.

This dependency-free module is also vendored in collector/legacy_word.py; keep
the two copies identical so either application can be deployed independently.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

MAX_BYTES = 12 * 1024 * 1024
CONVERSION_TIMEOUT = 90


class WordConversionError(ValueError):
    pass


def find_soffice() -> str:
    configured = os.environ.get('LIBREOFFICE_PATH')
    if configured:
        if Path(configured).is_file():
            return configured
        raise WordConversionError('converter_unavailable')
    # The console launcher waits for soffice.bin on Windows.
    for name in ('soffice.com', 'soffice', 'libreoffice', 'soffice.exe'):
        executable = shutil.which(name)
        if executable:
            return executable
    for root in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)')):
        if root:
            for name in ('soffice.com', 'soffice.exe'):
                candidate = Path(root) / 'LibreOffice' / 'program' / name
                if candidate.is_file():
                    return str(candidate)
    candidate = Path('/Applications/LibreOffice.app/Contents/MacOS/soffice')
    if candidate.is_file():
        return str(candidate)
    raise WordConversionError('converter_unavailable')


def _stop_conversion(process: subprocess.Popen) -> None:
    if os.name == 'nt':
        # Only terminate the process tree belonging to this private conversion.
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=5, check=False)
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def convert_legacy_word(raw: bytes, *, timeout: float = CONVERSION_TIMEOUT) -> bytes:
    if not raw or len(raw) > MAX_BYTES:
        raise WordConversionError('attachment_too_large' if raw else 'empty')
    executable = find_soffice()
    with tempfile.TemporaryDirectory(prefix='procurement-word-') as folder:
        root = Path(folder)
        source = root / 'source.doc'
        source.write_bytes(raw)
        profile = root / 'profile'
        profile.mkdir()
        # A fresh private profile prevents desktop-session collisions and
        # blocks document macros without changing the user's Office settings.
        (profile / 'user').mkdir()
        (profile / 'user' / 'registrymodifications.xcu').write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            '</item></oor:items>', encoding='utf-8')
        command = [executable, '-env:UserInstallation=' + profile.as_uri(),
                   '--headless', '--nologo', '--nodefault', '--norestore',
                   '--convert-to', 'pdf:writer_pdf_Export', '--outdir', str(root), str(source)]
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        try:
            # Do not inherit the service's redirected console handles. The
            # Windows console launcher must have explicit stdin and drained
            # output pipes to behave the same in a background service.
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
        except OSError as exc:
            raise WordConversionError('converter_unavailable') from exc
        try:
            process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _stop_conversion(process)
            process.communicate(timeout=5)
            raise WordConversionError('conversion_timeout') from exc
        target = root / 'source.pdf'
        if process.returncode != 0 or not target.is_file():
            raise WordConversionError('conversion_failed')
        if target.stat().st_size > MAX_BYTES:
            raise WordConversionError('converted_document_too_large')
        result = target.read_bytes()
        if not result.startswith(b'%PDF'):
            raise WordConversionError('conversion_failed')
        return result
