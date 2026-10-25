"""Install a persistent Tuya metadata correction using HA's supported quirk loader."""
import hashlib
import json
from pathlib import Path
import shutil
import time

PRODUCT_ID = 'vSu2uIvNamkj9gdG'


def install(config_dir, data_dir, client):
    devices = client.call('config/device_registry/list')
    if not any(d.get('model_id') == PRODUCT_ID for d in devices):
        return
    source = Path(__file__).resolve().parents[1] / 'ha/tuya_quirks/casa3d_voltage.py'
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    status_path = data_dir / 'voltage-fix.json'
    try:
        status = json.loads(status_path.read_text())
    except (OSError, ValueError):
        status = {}
    target = config_dir / 'tuya_quirks/casa3d_voltage.py'
    if target.exists() and target.read_bytes() == content and status.get('applied_hash') == digest:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != content:
        backup = data_dir / ('voltage-quirk-before-' + str(int(time.time())) + '.py')
        shutil.copy2(target, backup)
    temporary = target.with_suffix('.tmp')
    temporary.write_bytes(content)
    temporary.replace(target)
    entries = json.loads((config_dir / '.storage/core.config_entries').read_text())['data']['entries']
    for entry in entries:
        if entry['domain'] == 'tuya':
            client.call('call_service', domain='homeassistant', service='reload_config_entry',
                        service_data={'entry_id': entry['entry_id']}, blocking=True)
    status_path.write_text(json.dumps({'applied_hash': digest, 'updated_at': time.time(),
                                      'status': 'Correcao de escala do produto 16A Estimate Socket aplicada'}))
