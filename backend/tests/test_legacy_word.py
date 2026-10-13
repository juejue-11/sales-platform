import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.legacy_word import WordConversionError, convert_legacy_word, find_soffice


def test_converter_configuration_is_validated(monkeypatch):
    monkeypatch.setenv('LIBREOFFICE_PATH', 'nonexistent-converter')
    with pytest.raises(WordConversionError, match='converter_unavailable'):
        find_soffice()


def test_conversion_uses_private_profile_and_removes_temporary_files():
    runs = []
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            self.command = command
            runs.append(command)
            assert kwargs['stdin'] == subprocess.DEVNULL
            assert kwargs['stdout'] == subprocess.PIPE
            assert kwargs['stderr'] == subprocess.PIPE
            source = Path(command[-1])
            assert source.read_bytes() == b'original doc'
            profile_arg = next(arg for arg in command if arg.startswith('-env:UserInstallation='))
            assert profile_arg.endswith('/profile')
            assert '--headless' in command
            assert 'MacroSecurityLevel' in (source.parent / 'profile/user/registrymodifications.xcu').read_text()
            source.with_suffix('.pdf').write_bytes(b'%PDF-converted')
        def communicate(self, timeout):
            assert timeout == 90
            return b'', b''
    with patch('backend.legacy_word.find_soffice', return_value='soffice'), \
            patch('backend.legacy_word.subprocess.Popen', Process):
        assert convert_legacy_word(b'original doc') == b'%PDF-converted'
    assert not Path(runs[0][-1]).parent.exists()


def test_timeout_terminates_only_owned_conversion():
    class Process:
        attempts = 0
        def communicate(self, timeout):
            self.attempts += 1
            if self.attempts == 1:
                raise subprocess.TimeoutExpired('soffice', timeout)
            return b'', b''
    process = Process()
    with patch('backend.legacy_word.find_soffice', return_value='soffice'), \
            patch('backend.legacy_word.subprocess.Popen', return_value=process), \
            patch('backend.legacy_word._stop_conversion') as stop:
        with pytest.raises(WordConversionError, match='conversion_timeout'):
            convert_legacy_word(b'original doc', timeout=0.1)
    stop.assert_called_once_with(process)


@pytest.mark.parametrize('output', [None, b'not PDF', b'%PDF-too-big'])
def test_converter_exit_success_does_not_hide_invalid_or_oversized_output(output):
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            if output is not None:
                Path(command[-1]).with_suffix('.pdf').write_bytes(output)
        def communicate(self, timeout):
            return b'', b''
    with patch('backend.legacy_word.find_soffice', return_value='soffice'), \
            patch('backend.legacy_word.subprocess.Popen', Process), \
            patch('backend.legacy_word.MAX_BYTES', 8):
        with pytest.raises(WordConversionError):
            convert_legacy_word(b'doc')
