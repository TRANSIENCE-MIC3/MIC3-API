"""Execute the shared copy script in its real image, isolating network transfer."""
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
IMAGE = yaml.safe_load((ROOT / 'deploy/helm/model-run/values.yaml').read_text())['copy']['image']


@pytest.mark.parametrize('case,copy_exit,expected', [
    ('empty',0,0), ('directories',0,0), ('nonempty',0,0),
    ('zero-byte',0,0), ('nonempty',9,9),
])
def test_empty_outputs_skip_transfer_but_real_transfer_failures_propagate(tmp_path, case, copy_exit, expected):
    docker = shutil.which('docker')
    if not docker:
        pytest.skip('Docker required for the copy container contract')
    if case == 'directories':
        (tmp_path / 'nested').mkdir()
    elif case in ('nonempty','zero-byte'):
        (tmp_path / 'result.txt').write_text('result' if case == 'nonempty' else '')
    # Helm's YAML block scalar normalizes Windows checkout line endings too.
    script = tmp_path.parent / (tmp_path.name + '-copy.sh')
    script.write_text((ROOT / 'deploy/helm/model-run/files/copy.sh').read_text(), newline='\n')
    # Use the image's actual shell/mc listing and the production script.
    # A substitute mc executable makes transfer deterministic without credentials.
    command = '''export REAL_MC=$(command -v mc)
mkdir -p /tmp/testbin
printf '%s\\n' '#!/bin/sh' 'if [ "$1" = cp ]; then echo TRANSFER_CALLED; exit "$COPY_EXIT"; fi' 'if [ "$1" = ls ]; then exec "$REAL_MC" "$@"; fi' > /tmp/testbin/mc
chmod +x /tmp/testbin/mc
export PATH=/tmp/testbin:$PATH
. /copy.sh'''
    result = subprocess.run([docker,'run','--rm','--entrypoint','/bin/sh',
        '-v',f'{tmp_path}:/outputs:ro',
        '-v',f'{script}:/copy.sh:ro',
        '-e',f'COPY_EXIT={copy_exit}', '-e','S3_ENDPOINT=http://unused',
        '-e','AWS_ACCESS_KEY_ID=test', '-e','AWS_SECRET_ACCESS_KEY=test',
        '-e','S3_BUCKET=test','-e','MODEL_ID=test','-e','MODEL_IMAGE_SHA=test','-e','RUN_ID=test',
        IMAGE,'-c',command],capture_output=True,text=True,timeout=180)
    assert result.returncode == expected, result.stdout + result.stderr
    assert ('TRANSFER_CALLED' in result.stdout) == (case in ('nonempty','zero-byte'))
