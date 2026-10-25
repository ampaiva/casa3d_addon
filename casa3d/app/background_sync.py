"""Periodic synchronization independent of the browser being open."""
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import tuya_rooms, energy_learning, voltage_fix
from .ha_client import HAClient, supervisor_token

_LOG = logging.getLogger(__name__)


def learn(client, config_dir, data_dir):
    entities = client.call('config/entity_registry/list')
    devices = {d['id']: d for d in client.call('config/device_registry/list')}
    states = {s['entity_id']: s for s in client.call('get_states')}
    power = [e for e in entities if not e.get('disabled_by') and not e.get('hidden_by')
             and (e.get('device_class') or e.get('original_device_class') or states.get(e['entity_id'], {}).get('attributes', {}).get('device_class')) == 'power'
             and e.get('device_id') in devices]
    meters = {}
    loads = {}
    for entry in power:
        entity = entry['entity_id']
        device = devices[entry['device_id']]
        title = device.get('name_by_user') or device.get('name') or entity
        attributes = states.get(entity, {}).get('attributes', {})
        channel = energy_learning.phase_channel(entity, attributes.get('friendly_name', ''))
        if channel and ('medidor' in title.casefold() or 'meter' in title.casefold()):
            meters[entity] = {'title': title, 'channel': channel, 'device_id': device['id']}
        elif not channel and not entry.get('entity_category') and 'medidor' not in title.casefold() and 'meter' not in title.casefold():
            loads[entity] = entry['device_id']
    output = {'updated_at': time.time(), 'devices': {}, 'meters': meters}
    if meters and loads:
        start = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        requested = list(meters) + list(loads)
        histories = client.history(start, requested)
        points = {history[0]['entity_id']: history for history in histories if history}
        parsed = {entity: energy_learning.series(points.get(entity, []),
                  states.get(entity, {}).get('attributes', {}).get('unit_of_measurement')) for entity in requested}
        meter_points = {entity: parsed[entity] for entity in meters}
        for entity, device_id in loads.items():
            result = energy_learning.compare(parsed[entity], meter_points)
            result['load_entity'] = entity
            if result.get('candidate'):
                candidate = result['candidate']
                result['meter'] = {**meters[candidate['entity']], 'entity': candidate['entity']}
            # Multiple load channels cannot safely determine a device-wide supply.
            if device_id in output['devices']:
                output['devices'][device_id] = {'status': 'Dispositivo com multiplas medicoes; analisar por canal'}
            else:
                output['devices'][device_id] = result
    else:
        output['status'] = 'Sem medidores por fase ou cargas com potencia disponiveis'
    path = data_dir / 'energy-learning.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    temporary.replace(path)


def run_once(config_dir, data_dir, controls_path, options, client_factory=HAClient):
    data_dir.mkdir(parents=True, exist_ok=True)
    with client_factory() as client:
        if options.get('tuya_voltage_fix', True):
            try:
                voltage_fix.install(config_dir, data_dir, client)
            except Exception as exc:
                _LOG.warning('Correcao de escala Tuya indisponivel (%s)', type(exc).__name__)
        if options.get('tuya_room_sync', True):
            try:
                controls = json.loads(controls_path.read_text()) if controls_path.exists() else {}
                explicit = controls.get('location_mapping', {}).get('tuya_rooms', {})
                tuya_rooms.sync(config_dir, data_dir, client, explicit=explicit)
            except Exception as exc:
                state = tuya_rooms.read_state(data_dir)
                state.update(last_attempt=time.time(), error='Falha na sincronizacao: ' + type(exc).__name__)
                tuya_rooms.write_state(data_dir, state)
                _LOG.warning('Sincronizacao Tuya indisponivel (%s)', type(exc).__name__)
        if options.get('energy_learning', True):
            try:
                learn(client, config_dir, data_dir)
            except Exception as exc:
                _LOG.warning('Comparacao de energia indisponivel (%s)', type(exc).__name__)
                path = data_dir / 'energy-learning.json'
                # Never leave stale candidates presented as current after a failed refresh.
                path.write_text(json.dumps({'updated_at': time.time(), 'devices': {},
                                            'status': 'Comparacao indisponivel: ' + type(exc).__name__}))


def start(config_dir, data_dir, controls_path, options_path):
    if not supervisor_token():
        return
    def worker():
        while True:
            try:
                options = json.loads(options_path.read_text()) if options_path.exists() else {}
                run_once(config_dir, data_dir, controls_path, options)
            except Exception as exc:
                _LOG.warning('API do Home Assistant indisponivel (%s)', type(exc).__name__)
            time.sleep(900)
    threading.Thread(target=worker, daemon=True, name='casa3d-sync').start()
