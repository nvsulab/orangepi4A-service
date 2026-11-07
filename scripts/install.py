#!/usr/bin/env python3
"""Install this prepared local project. Run with sudo; no downloads or Git commits."""
from pathlib import Path
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent.parent
SERVICES = ['dashboard', 'camera-stream', 'ttyd', 'myapp', 'led-bouncing']
LINKS = {
    '/var/www/dashboard': PROJECT / 'dashboard',
    '/var/www/led-panel': PROJECT / 'led-panel',
    '/usr/local/share/mjpg-streamer/www': PROJECT / 'camera/www',
    '/usr/local/bin/start_camera.sh': PROJECT / 'scripts/start_camera.sh',
    '/usr/local/bin/led_service': PROJECT / 'led-service',
}

def run(*args, check=True):
    return subprocess.run(args, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def restore(backup):
    manifest = json.loads((backup / 'manifest.json').read_text())
    for name in SERVICES:
        run('systemctl', 'stop', name, check=False)
    # Restore the virtual environment first, while both saved directories exist.
    if manifest.get('venv_moved'):
        saved_led = backup / 'paths/var/www/led-panel'
        if not saved_led.exists():
            saved_led = Path('/var/www/led-panel')
        venv = PROJECT / 'led-panel/venv'
        if venv.exists():
            shutil.move(str(venv), str(saved_led / 'venv'))
    for original in reversed(manifest['paths']):
        path = Path(original)
        saved = backup / 'paths' / original.lstrip('/')
        if path.is_symlink():
            path.unlink()
        if saved.exists() or saved.is_symlink():
            shutil.move(str(saved), str(path))
    for filename in manifest['configs']:
        destination = Path(filename)
        saved = backup / 'configs' / filename.lstrip('/')
        if saved.exists():
            shutil.copy2(saved, destination)
        elif destination.exists():
            destination.unlink()
    for name, state in manifest['states'].items():
        if state['enabled'] != 'enabled':
            run('systemctl', 'disable', name, check=False)
    run('systemctl', 'daemon-reload')
    for name, state in manifest['states'].items():
        if state['active']:
            run('systemctl', 'start', name)
    print('Original files and service states restored from', backup)


def install():
    if any(Path(old).is_symlink() and Path(old).resolve() == new.resolve() for old, new in LINKS.items()):
        raise SystemExit('Project already installed. Edit its files and restart the relevant service.')
    # Preflight all file and service requirements before stopping anything.
    for old, new in LINKS.items():
        if not Path(old).exists() or not new.exists():
            raise SystemExit('Missing source or destination: ' + old)
    if not Path('/var/www/led-panel/venv/bin/python').exists():
        raise SystemExit('Existing LED virtual environment is missing')
    if (PROJECT / 'led-panel/venv').exists():
        raise SystemExit('Destination venv already exists; inspect before installing')
    ttyd_start = run('systemctl', 'cat', 'ttyd').stdout
    lines = re.findall(r'^ExecStart=(.+)$', ttyd_start, re.M)
    if not lines:
        raise SystemExit('Cannot find the existing ttyd command')
    # Keep the existing terminal authentication exclusively in /etc, outside Git.
    command = lines[-1]
    executable, arguments = command.split(' ', 1)
    terminal_command = executable + ' --index ' + str(PROJECT / 'terminal/index.html') + ' ' + arguments
    backup = Path('/var/backups') / ('local-portal-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    manifest = {'paths': [], 'configs': [], 'states': {}, 'venv_moved': False}
    def save():
        (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    for name in SERVICES:
        manifest['states'][name] = {
            'active': run('systemctl', 'is-active', name, check=False).returncode == 0,
            'enabled': run('systemctl', 'is-enabled', name, check=False).stdout.strip(),
        }
    save()
    try:
        for name in SERVICES:
            run('systemctl', 'stop', name)
        for old, new in LINKS.items():
            original = Path(old)
            saved = backup / 'paths' / old.lstrip('/')
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(original), str(saved))
            manifest['paths'].append(old); save()
            original.symlink_to(new, target_is_directory=new.is_dir())
        saved_venv = backup / 'paths/var/www/led-panel/venv'
        shutil.move(str(saved_venv), str(PROJECT / 'led-panel/venv'))
        manifest['venv_moved'] = True; save()
        for name in SERVICES:
            # A dedicated drop-in leaves the original service and other overrides intact.
            destination = Path('/etc/systemd/system') / (name + '.service.d/90-local-portal.conf')
            saved = backup / 'configs' / str(destination).lstrip('/')
            saved.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.copy2(destination, saved)
            manifest['configs'].append(str(destination)); save()
            destination.parent.mkdir(parents=True, exist_ok=True)
            if name == 'ttyd':
                text = '[Service]\nExecStart=\nExecStart=' + terminal_command + '\n'
            else:
                unit = (PROJECT / 'systemd' / (name + '.service')).read_text().replace('@PROJECT_DIR@', str(PROJECT))
                values = [line for line in unit.splitlines() if line.startswith(('WorkingDirectory=', 'ExecStart='))]
                text = '[Service]\nExecStart=\n' + '\n'.join(values) + '\n'
            destination.write_text(text)
            destination.chmod(0o600 if name == 'ttyd' else 0o644)
        for script in (PROJECT / 'scripts').glob('*.sh'):
            script.chmod(0o755)
        run('systemctl', 'daemon-reload')
        for name in ['dashboard', 'camera-stream', 'ttyd']:
            run('systemctl', 'start', name)
        # This service was disabled before migration; start it so the LED card is usable.
        # The application acquires SPI only while browser pages hold an active session.
        run('systemctl', 'enable', 'myapp')
        run('systemctl', 'start', 'myapp')
        import time
        time.sleep(3)
        for name in ['dashboard', 'camera-stream', 'ttyd', 'myapp']:
            if run('systemctl', 'is-active', name, check=False).returncode != 0:
                raise RuntimeError(name + ' failed to stay active; inspect journalctl -u ' + name)
    except Exception:
        print('Install failed; restoring original files and service states.', file=sys.stderr)
        restore(backup)
        raise
    print('Installed:', PROJECT)
    print('Open: http://192.168.12.1:57575/')
    print('Rollback: sudo python3 ' + str(PROJECT / 'scripts/install.py') + ' --rollback ' + str(backup))

if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Run this installer with sudo')
    if len(sys.argv) == 3 and sys.argv[1] == '--rollback':
        restore(Path(sys.argv[2]))
    elif len(sys.argv) == 1:
        install()
    else:
        raise SystemExit('Usage: sudo python3 scripts/install.py [--rollback BACKUP_DIR]')
