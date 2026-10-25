"""Release-only tooling; no cluster access. Registry failures always stop delivery."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

REPOSITORIES = {role: f"ghcr.io/transience-mic3/mic3-{role}" for role in ('api', 'publisher', 'worker')}
DIGEST = r'sha256:[0-9a-f]{64}'


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()


def validate_identity(tag, commit):
    if not re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', tag):
        raise ValueError('Invalid stable release tag')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Invalid source commit')


def validate(record, tag, commit):
    validate_identity(tag, commit)
    if record.get('release_tag') != tag or record.get('commit') != commit:
        raise ValueError('Release tag/source mismatch')
    if type(record.get('format_version')) is not int:
        raise ValueError('Invalid record format')
    if record['format_version'] == 1:
        images = {'api': {key: record.get(key) for key in ('image_repository', 'image_ref')}}
    elif record['format_version'] == 2:
        images = record.get('images')
        if not isinstance(images, dict) or set(images) != set(REPOSITORIES):
            raise ValueError('All three images are required')
    else:
        raise ValueError('Unsupported record format')
    for role, image in images.items():
        repository = REPOSITORIES[role]
        if not isinstance(image, dict) or image.get('image_repository') != repository:
            raise ValueError(f'Unexpected {role} repository')
        ref = image.get('image_ref')
        if not isinstance(ref, str) or not re.fullmatch(re.escape(f'{repository}:{tag[1:]}@') + DIGEST, ref):
            raise ValueError(f'Invalid {role} immutable reference')
    return images


def preflight(version):
    """Check every tag before any push; only confirmed absence permits publication."""
    for repository in REPOSITORIES.values():
        ref = f'{repository}:{version}'
        try:
            run('docker', 'manifest', 'inspect', ref)
        except subprocess.CalledProcessError as error:
            if error.stderr.strip() not in ('manifest unknown', f'no such manifest: {ref}'):
                raise RuntimeError('Registry lookup failed; publication refused') from error
        else:
            raise ValueError('Version image exists; recover release.json from verified evidence or use a new version')


def verify_remote(images):
    for image in images.values():
        run('docker', 'manifest', 'inspect', image['image_ref'])


def discover(tag, repository, record_path):
    try:
        metadata = json.loads(run('gh', 'api', f'repos/{repository}/releases/tags/{tag}'))
    except subprocess.CalledProcessError as error:
        if '(HTTP 404)' not in error.stderr:
            raise RuntimeError('GitHub lookup failed; publication refused') from error
        return True
    if (metadata.get('draft') is not False or metadata.get('prerelease') is not False or
        sum(asset.get('name') == 'release.json' for asset in metadata.get('assets', [])) != 1):
        raise ValueError('Existing release must contain exactly one stable release.json')
    run('gh', 'release', 'download', tag, '--repo', repository,
        '--pattern', 'release.json', '--dir', str(record_path.parent))
    if not record_path.is_file():
        raise ValueError('Release record download missing')
    return False


def publish(tag, commit):
    validate_identity(tag, commit)
    # Repeat the preflight immediately before publication, after lengthy tests.
    preflight(tag[1:])
    print(f'Publishing {tag} from {commit}', flush=True)
    record = {'format_version': 2, 'release_tag': tag, 'commit': commit, 'images': {}}
    # All local artifacts must exist before the first push.
    for role, repository in REPOSITORIES.items():
        local = json.loads(run('docker', 'image', 'inspect', f'{repository}:{tag[1:]}'))[0]
        labels = local['Config'].get('Labels') or {}
        if (local['Os'], local['Architecture']) != ('linux', 'amd64') or (
            labels.get('org.opencontainers.image.revision') != commit or
            labels.get('org.opencontainers.image.version') != tag[1:]
        ):
            raise ValueError(f'Incorrect local {role} artifact')
    for role, repository in REPOSITORIES.items():
        ref = f'{repository}:{tag[1:]}'
        run('docker', 'push', ref)
        local = json.loads(run('docker', 'image', 'inspect', ref))[0]
        digests = {value.removeprefix(repository+'@') for value in local.get('RepoDigests', [])
                   if value.startswith(repository+'@')}
        if len(digests) != 1 or not re.fullmatch(DIGEST, next(iter(digests))):
            raise ValueError(f'Missing or ambiguous published {role} digest')
        record['images'][role] = {'image_repository': repository, 'image_ref': ref+'@'+digests.pop()}
        # Preserve completed publication evidence even if a later push fails.
        print(f'Published {role}: {record["images"][role]["image_ref"]}', flush=True)
    images = validate(record, tag, commit)
    verify_remote(images)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['discover', 'preflight', 'publish', 'validate'])
    parser.add_argument('--record', type=Path, required=True)
    args = parser.parse_args()
    tag, commit = os.environ['RELEASE_TAG'], os.environ['COMMIT']
    validate_identity(tag, commit)
    if args.operation == 'discover':
        required = discover(tag, os.environ['GITHUB_REPOSITORY'], args.record)
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('build_required='+str(required).lower()+'\n')
    elif args.operation == 'preflight':
        preflight(tag[1:])
    elif args.operation == 'publish':
        if args.record.exists():
            raise ValueError('Refusing to replace a release record')
        record = publish(tag, commit)
        args.record.write_text(json.dumps(record, indent=2)+'\n')
    else:
        images = validate(json.loads(args.record.read_text()), tag, commit)
        verify_remote(images)
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('image_ref='+images['api']['image_ref']+'\n')
            output.write('execution_available='+str('worker' in images).lower()+'\n')
            for role in ('publisher', 'worker'):
                output.write(role+'_image_ref='+images.get(role, {}).get('image_ref', '')+'\n')
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(f'Release: `{tag}`\n\n')
            for role, image in images.items():
                summary.write(f'{role}: `{image["image_ref"]}`\n\n')
            if 'worker' not in images:
                summary.write('Legacy record: execution not deployed or verified.\n')


if __name__ == '__main__':
    main()
