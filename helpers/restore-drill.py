#!/usr/bin/env python3
"""Boot an authenticated off-host snapshot with its real application images offline."""
import argparse
from contextlib import nullcontext
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def helper(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def docker(*args, input=None, timeout=60):
    try:
        result = subprocess.run(['docker', *args], input=input, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'Docker {args[0]} timed out during the isolated restore drill') from None
    if result.returncode:
        # Arguments may include disposable clone credentials. Never print them,
        # application logs, or restored database contents into a workflow log.
        raise RuntimeError(f'Docker {args[0]} failed during the isolated restore drill')
    return result.stdout.strip()


# Which store a product's snapshot restores into. A product that migrates to
# PostgreSQL flips its entry here and nothing else in this file changes: the
# two halves of the drill share the envelope, the image authentication and the
# frontend check, and differ only in what they boot the data into.
BACKENDS = {'ormos': 'pocketbase', 'cazper': 'postgresql', 'komizo': 'pocketbase',
            'gdam': 'pocketbase', 'termcade': 'pocketbase', 'astry': 'pocketbase'}

# The application half of a PostgreSQL drill is product-owned by nature: only
# the product knows which variable carries its DSN and where it keeps blobs.
#
# The DSN arrives as a FILE, never as an env value: restored_database writes
# one 0600 file per restored role, and a live credential in an --env-file is
# one `docker inspect` away from a log. Blobs get their own tmpfs because the
# serving path stats the directory rather than creating it -- under /tmp it
# would not exist, and the API would refuse to start for the wrong reason.
POSTGRES_APPS = {
    'cazper': {
        'runtime_role': 'cazper_runtime',
        'dsn_file': '/run/secrets/database.url',
        'dsn_variable': 'CAZPER_DATABASE_URL_FILE',
        'blobs': '/blobs',
        'api_port': 8080,
        'health_path': '/health',
        'root': '/srv/public/app',
        'env': {'CAZPER_API_ADDR': ':8080', 'CAZPER_BLOB_DIRECTORY': '/blobs',
                'CAZPER_DEV': '', 'OPENAI_API_KEY': '', 'CLERK_SECRET_KEY': ''},
    },
}


def probe_endpoint(docker_run, watched, url, timeout=175):
    """Poll `url` from inside the watched container's network namespace.

    The namespace is the isolated one the store was started in, so nothing
    here is reachable from the host or from any other container.
    """
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if not json.loads(docker('inspect', watched))[0]['State']['Running']:
            raise RuntimeError('restored container exited before becoming healthy')
        try:
            body = docker_run(url)
            if time.monotonic() >= deadline:
                raise TimeoutError('restore health response arrived after its deadline')
            return body
        except RuntimeError:
            time.sleep(1)
    raise TimeoutError('restored service did not become healthy before its deadline')


def postgres_drill(project, export, key, pull=False):
    """Restore an authenticated snapshot into PostgreSQL and boot the product on it.

    Proves the same three things the PocketBase drill does -- the data comes
    back, the API serves against it, and the release's frontend artifact is a
    real document -- for a product whose store is PostgreSQL.
    """
    snapshot = helper('snapshot')
    receiver = helper('receive-backup')
    recovery = helper('postgres-recovery')
    config = POSTGRES_APPS[project]
    owner = {'gdam': 'aviorstudio', 'termcade': 'aviorstudio', 'astry': 'astrylogical'}.get(project, 'nicodes')
    containers = []
    report = {'project': project, 'network': 'none; shared isolated loopback only', 'published_ports': []}
    memory = Path('/proc/meminfo')
    if memory.exists():
        available = re.search(r'^MemAvailable:\s+(\d+) kB$', memory.read_text(), re.M)
        if not available or int(available[1]) < 768 * 1024:
            raise ValueError('restore drill requires 768 MiB of available host memory')
    with tempfile.TemporaryDirectory(prefix=f'{project}-restore-drill-') as directory:
        root = Path(directory)
        receiver.receive(export, root/'encrypted')
        payload = root/'payload'
        payload.mkdir(mode=0o700)
        # unseal authenticates the envelope, validates the payload against its
        # manifest and, for this backend, checks the PostgreSQL engine image
        # against the receipt. It reads the backend from the envelope rather
        # than being told, so an envelope of the other kind must be refused
        # here: booting a pocketbase snapshot through this path would find no
        # manifest and fail later, and further from the cause.
        evidence = snapshot.unseal(root/'encrypted', key, payload)
        if evidence.get('backend') != 'postgresql':
            raise ValueError('this product restores into PostgreSQL; the envelope is not a PostgreSQL snapshot')
        # The engine reference in a PostgreSQL envelope is the POSTGRES image,
        # not a product image, so the product revision comes from the
        # authenticated manifest rather than from a tag.
        manifest = evidence['postgresql']
        if manifest['project'] != project:
            raise ValueError('authenticated snapshot belongs to another product')
        revision = manifest['revision']
        if not re.fullmatch(r'[a-f0-9]{40}', revision):
            raise ValueError('authenticated snapshot does not name an exact product revision')
        # The components come from the manifest, not from a list here: it is
        # the authenticated statement of which images belong to this snapshot,
        # and validate_manifest has already bound each reference to this
        # project and revision.
        images = {}
        for component, declared in sorted(manifest['images'].items()):
            image = f'ghcr.io/{owner}/{project}-{component}:{revision}'
            if declared['reference'] != image:
                raise ValueError('authenticated snapshot declares a different application image')
            if pull:
                # Authenticate the encrypted project/revision before pulling.
                # Registry credentials stay on the host, outside every clone.
                docker('pull', '--quiet', image, timeout=180)
            images[component] = json.loads(docker('image', 'inspect', image))[0]['Id']
            # A tag is mutable; the snapshot named a digest. Booting whatever
            # currently answers to the tag would make the drill prove nothing
            # about the release the data came from.
            if images[component] != declared['image_id']:
                raise ValueError('application image differs from authenticated snapshot metadata')
        api_component = 'service' if 'service' in images else 'api'
        if 'gate' not in images or api_component not in images:
            raise ValueError('authenticated snapshot is missing an application or frontend image')
        uid = os.getuid() if os.getuid() else 65534
        prefix = project+'-restore-'+secrets.token_hex(8)
        common = ['--read-only', '--user', f'{uid}:{uid}', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
                  '--cpus=1', '--pids-limit=128', '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m']
        try:
            with recovery.restored_database(payload, project, revision, pull=pull) as restored:
                store = restored['container']
                dsn = restored['credentials'][config['runtime_role']]
                netns = ['--network', f'container:{store}']

                def wget(url):
                    name = prefix+'-probe'
                    containers.append(name)
                    try:
                        return docker('run', '--name', name, *netns, *common, '--memory=64m',
                                      '--entrypoint', '/usr/bin/wget', images['gate'],
                                      '-qO-', '-T', '3', url, timeout=15)
                    finally:
                        docker('rm', '-f', '-v', name)
                        containers.remove(name)

                environment = root/'clone.env'
                environment.write_text(''.join(f'{name}={value}\n' for name, value in
                                               {**config['env'], config['dsn_variable']: config['dsn_file']}.items()))
                environment.chmod(0o600)
                api = prefix+'-api'
                containers.append(api)
                docker('run', '-d', '--name', api, *netns, *common, '--memory=192m',
                       '--tmpfs', f'{config["blobs"]}:rw,noexec,nosuid,uid={uid},gid={uid},mode=0700,size=32m',
                       '--mount', f'type=bind,src={dsn},dst={config["dsn_file"]},readonly',
                       '--env-file', str(environment), images[api_component])
                probe_endpoint(wget, api, f'http://127.0.0.1:{config["api_port"]}{config["health_path"]}', timeout=120)
                if not json.loads(docker('inspect', api))[0]['State']['Running']:
                    raise RuntimeError('restored API did not remain running')
                gate = prefix+'-gate'
                containers.append(gate)
                docker('run', '-d', '--name', gate, *netns, *common, '--memory=96m',
                       '--entrypoint', '/usr/bin/caddy', images['gate'],
                       'file-server', '--root', config['root'], '--listen', '127.0.0.1:8088')
                html = probe_endpoint(wget, gate, 'http://127.0.0.1:8088/', timeout=45)
                if '<html' not in html.lower() or '<script' not in html.lower():
                    raise ValueError('the restored frontend artifact did not serve an application document')
                report.update({'revision': revision, 'images': {**images, 'engine': restored['engine']},
                               'archive_sha256': evidence['archive_sha256'], 'backend': 'postgresql',
                               'database_integrity': 'ok', 'database_restore': restored['database_restore'],
                               'api_boot': 'passed', 'frontend_artifact': 'passed'})
        finally:
            failed = []
            for name in reversed(containers):
                try:
                    docker('rm', '-f', '-v', name)
                except RuntimeError:
                    failed.append(name)
            if failed:
                raise RuntimeError('isolated restore container cleanup failed')
    report['cleanup'] = 'passed'
    return report


def drill(project, export, key, pull=False):
    if BACKENDS[project] == 'postgresql':
        return postgres_drill(project, export, key, pull)
    snapshot = helper('snapshot')
    receiver = helper('receive-backup')
    config = {
        'ormos': {'db': 'db', 'binary': '/app/pocketbase', 'port': 8095, 'api_port': 9080, 'root': '/srv/public/app'},
        'cazper': {'db': 'db', 'binary': '/app/cazper-pocketbase', 'port': 8090, 'api_port': 8080, 'root': '/srv/public/app'},
        'komizo': {'db': 'service', 'binary': '/app/komizo-service', 'port': 8090, 'api_port': 8090, 'root': '/srv/app'},
        'gdam': {'db': 'db', 'binary': '/usr/local/bin/pocketbase', 'port': 8090, 'api_port': 8080, 'root': '/srv/public/app'},
        'termcade': {'db': 'db', 'binary': '/usr/local/bin/pocketbase', 'port': 8090, 'api_port': 8080, 'root': '/srv/public/app'},
        'astry': {'db': 'pb', 'binary': '/usr/local/bin/pocketbase', 'port': 8090, 'api_port': 6060, 'root': '/srv/public/app'},
    }[project]
    owner = {'gdam': 'aviorstudio', 'termcade': 'aviorstudio', 'astry': 'astrylogical'}.get(project, 'nicodes')
    data_path = '/pb/pb_data' if project in {'gdam', 'termcade', 'astry'} else '/app/pb_data'
    health_path = '/healthz' if project in {'gdam', 'termcade', 'astry'} else '/health'
    containers = []
    report = {'project': project, 'network': 'none; shared isolated loopback only', 'published_ports': []}
    memory = Path('/proc/meminfo')
    if memory.exists():
        available = re.search(r'^MemAvailable:\s+(\d+) kB$', memory.read_text(), re.M)
        if not available or int(available[1]) < 768 * 1024:
            raise ValueError('restore drill requires 768 MiB of available host memory')
    with tempfile.TemporaryDirectory(prefix=f'{project}-restore-drill-') as directory:
        root = Path(directory)
        receiver.receive(export, root/'encrypted')
        restored = root/'pb_data'
        restored.mkdir(mode=0o700)
        evidence = snapshot.unseal(root/'encrypted', key, restored)
        match = re.fullmatch(rf'ghcr.io/{owner}/{project}-{config["db"]}:([a-f0-9]{{40}})', evidence['image_reference'])
        if not match:
            raise ValueError('authenticated snapshot image does not belong to this product')
        revision = match[1]
        components = [config['db'], 'gate'] + ([] if project == 'komizo' else ['api'])
        images = {}
        for component in components:
            image = f'ghcr.io/{owner}/{project}-{component}:{revision}'
            if pull:
                # Authenticate the encrypted project/revision before pulling.
                # Registry credentials stay on the host, outside every clone.
                docker('pull', '--quiet', image, timeout=180)
            images[component] = json.loads(docker('image', 'inspect', image))[0]['Id']
            if component == config['db'] and images[component] != evidence['image_id']:
                raise ValueError('restored database image differs from authenticated snapshot metadata')
        # Root-host installations give the clone to nobody. Local verification
        # uses the caller's UID, still nonroot in the container.
        uid = os.getuid() if os.getuid() else 65534
        if os.getuid() == 0:
            for path in [restored, *restored.rglob('*')]:
                os.chown(path, uid, uid)
        password = secrets.token_hex(32)
        env = {
            'ORMOS_ADMIN_USER': 'restore@verification.invalid', 'ORMOS_ADMIN_PASSWORD': password,
            'ORMOS_RELAY_USER': 'restore-relay@verification.invalid', 'ORMOS_RELAY_PASSWORD': password,
            'ORMOS_PB_USER': 'restore@verification.invalid', 'ORMOS_PB_PASSWORD': password,
            'ORMOS_PB_URL': f'http://127.0.0.1:{config["port"]}', 'ORMOS_ADDR': ':9080',
            'ORMOS_TICKET_KEY': secrets.token_hex(32),
            'CAZPER_API_ADDR': ':8080', 'POCKETBASE_URL': 'http://127.0.0.1:8090',
            'CAZPER_PB_ADMIN_EMAIL': 'restore@verification.invalid', 'CAZPER_PB_ADMIN_PASSWORD': password,
            'CAZPER_DEV': '', 'OPENAI_API_KEY': '', 'CLERK_SECRET_KEY': '', 'KOMIZO_SIGNING_KEY': '',
        }
        if project in {'gdam', 'termcade', 'astry'}:
            env.update({
                'PB_URL': 'http://127.0.0.1:8090', 'PB_ADMIN_EMAIL': 'restore@verification.invalid', 'PB_ADMIN_PASSWORD': password,
                'GDAM_API_ADDR': ':8080', 'TERMCADE_API_ADDR': ':8080', 'TERMCADE_PROXY_HOPS': '2', 'TERMCADE_ENVIRONMENT': 'development',
                'TERMCADE_COOKIE_SECURE': 'off', 'TERMCADE_ALLOWED_ORIGINS': 'http://127.0.0.1:8088',
                'CLERK_SECRET_KEY': 'sk_test_restore_verification', 'CLERK_PUBLISHABLE_KEY': 'pk_test_Y2xlcmsucmVzdG9yZS5pbnZhbGlkJA',
                'CLERK_AUTHORIZED_PARTIES': 'http://127.0.0.1:8088', 'CORS_ALLOWED_ORIGINS': 'http://127.0.0.1:8088',
                'ASTRY_PB_ADMIN_EMAIL': 'restore@verification.invalid', 'ASTRY_PB_ADMIN_PASSWORD': password,
                'LISTEN_ADDR': ':6060', 'TAROT_SIGNING_KEY': secrets.token_hex(32),
            })
        if project == 'komizo':
            encryption = os.environ.get('PB_ENCRYPTION_KEY')
            if encryption is None:
                raise ValueError('supply the settings encryption key explicitly for the restore drill')
            env['PB_ENCRYPTION_KEY'] = encryption
        environment = root/'clone.env'
        environment.write_text(''.join(f'{name}={value}\n' for name, value in env.items()))
        environment.chmod(0o600)
        settings_flags = ['--encryptionEnv=PB_ENCRYPTION_KEY'] if project == 'komizo' else []
        prefix = project+'-restore-'+secrets.token_hex(8)
        common = ['--read-only', '--user', f'{uid}:{uid}', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
                  '--cpus=1', '--pids-limit=128', '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m']
        private_env = ['--env-file', str(environment)]
        mount = ['--mount', f'type=bind,src={restored},dst={data_path}']
        try:
            # This affects only the decrypted disposable clone.
            bootstrap = prefix+'-bootstrap'
            containers.append(bootstrap)
            docker('run', '--name', bootstrap, '--network=none', *common, '--memory=192m', *private_env, *mount,
                   '--entrypoint', config['binary'], images[config['db']], 'superuser', 'upsert',
                   'restore@verification.invalid', password, f'--dir={data_path}', *settings_flags, timeout=180)
            db = prefix+'-db'
            containers.append(db)
            command = ['serve', f'--http=127.0.0.1:{config["port"]}', f'--dir={data_path}', *settings_flags]
            if project == 'ormos':
                command += ['--automigrate=false', '--hooksDir=/app/pb_hooks', '--migrationsDir=/app/pb_migrations']
            if project in {'gdam', 'termcade', 'astry'}:
                command += ['--automigrate=false', '--migrationsDir=/pb/pb_migrations']
                if project == 'astry': command += ['--hooksDir=/pb/pb_hooks']
            docker('run', '-d', '--name', db, '--network=none', *common, '--memory=192m', *private_env, *mount,
                   '--entrypoint', config['binary'], images[config['db']], *command)
            # The gate image supplies wget for the distroless service too.
            # Its helper processes share only this isolated network namespace.
            def probe(url, timeout=175):
                deadline = time.monotonic()+timeout
                while time.monotonic() < deadline:
                    state = json.loads(docker('inspect', db))[0]['State']
                    if not state['Running']:
                        raise RuntimeError('restored database exited before becoming healthy')
                    try:
                        name = prefix+'-probe'
                        if name not in containers:
                            containers.append(name)
                        body = docker('run', '--name', name, '--network', f'container:{db}', *common, '--memory=64m',
                                      '--entrypoint', '/usr/bin/wget', images['gate'], '-qO-', '-T', '3', url, timeout=15)
                        if time.monotonic() >= deadline:
                            raise TimeoutError('restore health response arrived after its deadline')
                        return body
                    except RuntimeError:
                        time.sleep(1)
                    finally:
                        docker('rm', '-f', '-v', name)
                        containers.remove(name)
                raise TimeoutError('restored service did not become healthy before its deadline')
            probe(f'http://127.0.0.1:{config["port"]}/api/health')
            if project != 'komizo':
                api = prefix+'-api'
                containers.append(api)
                docker('run', '-d', '--name', api, '--network', f'container:{db}', *common, '--memory=128m', *private_env, images['api'])
                probe(f'http://127.0.0.1:{config["api_port"]}{health_path}', timeout=60)
                if not json.loads(docker('inspect', api))[0]['State']['Running']:
                    raise RuntimeError('restored API did not remain running')
            gate = prefix+'-gate'
            containers.append(gate)
            docker('run', '-d', '--name', gate, '--network', f'container:{db}', *common, '--memory=96m',
                   '--entrypoint', '/usr/bin/caddy', images['gate'], 'file-server', '--root', config['root'], '--listen', '127.0.0.1:8088')
            html = probe('http://127.0.0.1:8088/', timeout=45)
            if '<html' not in html.lower() or '<script' not in html.lower():
                raise ValueError('the restored frontend artifact did not serve an application document')
            report.update({'revision': revision, 'images': images, 'archive_sha256': evidence['archive_sha256'],
                           'database_integrity': 'ok', 'database_boot': 'passed', 'api_boot': 'passed', 'frontend_artifact': 'passed'})
        finally:
            failed = []
            for name in reversed(containers):
                try:
                    docker('rm', '-f', '-v', name)
                except RuntimeError:
                    failed.append(name)
            if failed:
                raise RuntimeError('isolated restore container cleanup failed')
    report['cleanup'] = 'passed'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', choices=['ormos', 'cazper', 'komizo', 'gdam', 'termcade', 'astry'], required=True)
    parser.add_argument('--export', type=Path, required=True)
    parser.add_argument('--key', type=Path, required=True)
    parser.add_argument('--lock', type=Path, help='root-owned host lock for scheduled drills')
    parser.add_argument('--pull', action='store_true', help='pull authenticated exact-revision images on the restore runner')
    args = parser.parse_args()
    os.umask(0o077)
    def interrupted(number, _frame):
        # Ignore a second signal while the first unwinds owned container cleanup.
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
            signal.signal(signum, signal.SIG_IGN)
        raise InterruptedError(f'restore drill interrupted by signal {number}')
    for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
        signal.signal(number, interrupted)
    # Includes the stream receive deadline; a stalled SSH sender cannot retain
    # a root worker indefinitely. Cleanup runs when the alarm unwinds the drill.
    signal.alarm(900)
    with args.lock.open('a') if args.lock else nullcontext() as lock:
        if lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='restore-input-') as directory:
            export = args.export
            if str(export) == '-':
                export = Path(directory)/'export.tar'
                total = 0
                with export.open('xb') as output:
                    while block := sys.stdin.buffer.read(1024*1024):
                        total += len(block)
                        if total > 17 * 1024**3 + 1024**2 or shutil.disk_usage(directory).free < len(block) + 512 * 1024**2:
                            raise ValueError('restore input exceeds the size or disk headroom bound')
                        output.write(block)
            print(json.dumps(drill(args.project, export, args.key, args.pull), indent=2))
    signal.alarm(0)


if __name__ == '__main__':
    main()
