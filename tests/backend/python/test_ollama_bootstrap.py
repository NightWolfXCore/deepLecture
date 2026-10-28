import os
from pathlib import Path
import subprocess
import time
import pytest

SCRIPT = Path(__file__).resolve().parents[3] / 'infra/llm/start-ollama.sh'


def test_missing_gguf_exits_before_server_starts(tmp_path):
    result = subprocess.run(['sh',str(SCRIPT)], env={**os.environ,'GGUF_PATH':str(tmp_path/'missing')}, capture_output=True,text=True)
    assert result.returncode == 1
    assert 'Missing GGUF' in result.stderr


@pytest.mark.parametrize('fail_import',[False,True])
def test_ollama_import_is_required_before_readiness(tmp_path,fail_import):
    binary=tmp_path/'ollama'
    binary.write_text('''#!/bin/sh
case "$1" in
 serve) exec sleep 20;;
 list) exit 0;;
 create) echo IMPORT_CALLED; exit "${FAKE_IMPORT_EXIT:-0}";;
 show) exit 0;;
esac
''')
    binary.chmod(0o755)
    model=tmp_path/'model.gguf'; model.write_bytes(b'fake test file only')
    env={**os.environ,'PATH':str(tmp_path)+os.pathsep+os.environ['PATH'],'GGUF_PATH':str(model),'FAKE_IMPORT_EXIT':'1' if fail_import else '0'}
    log=tmp_path/'log'
    with log.open('w') as out:
        process=subprocess.Popen(['sh',str(SCRIPT)],env=env,stdout=out,stderr=out)
        try:
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                text=log.read_text()
                if ('Local model' in text) or process.poll() is not None: break
                time.sleep(.02)
            assert 'IMPORT_CALLED' in log.read_text()
            if fail_import:
                assert process.wait(timeout=2)==1
                assert 'Local model' not in log.read_text()
            else:
                assert 'Local model deeplecture-qwen3:4b is available' in log.read_text()
                assert process.poll() is None
        finally:
            if process.poll() is None: process.terminate()
            process.wait(timeout=3)
