"""Append unambiguous appliance counters to HA Energy through its supported API."""
import json
import math
import time
from pathlib import Path

from .electrical_catalog import normalized
from .meter_selection import physical_key

ENERGY_UNITS = {'Wh', 'kWh', 'MWh'}


def references(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.startswith('stat_') and isinstance(item, str):
                yield item
            else:
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def identity(device):
    return json.dumps(physical_key(device), separators=(',', ':'))


def plan(prefs, entities, devices, states, statistics, ignored=()):
    """Keep existing selections; refuse duplicate devices and ambiguous counters."""
    devices = {d['id']: d for d in devices if not d.get('disabled_by')}
    entities = {e['entity_id']: e for e in entities}
    stats = {s['statistic_id']: s for s in statistics}
    existing = {r['stat_consumption'] for r in prefs.get('device_consumption', [])}
    source_ids = set(references(prefs.get('energy_sources', [])))
    def key(entity):
        device = devices.get(entities.get(entity, {}).get('device_id'))
        return identity(device) if device else None
    occupied = {key(e) for e in existing | source_ids} - {None}
    candidates, pending = {}, {}
    for eid, entry in entities.items():
        device = devices.get(entry.get('device_id'))
        if not device or not eid.startswith('sensor.') or entry.get('disabled_by') or entry.get('hidden_by') or entry.get('entity_category'):
            continue
        attrs = states.get(eid, {}).get('attributes', {})
        if (entry.get('device_class') or entry.get('original_device_class') or attrs.get('device_class')) != 'energy':
            continue
        physical = identity(device)
        if physical in occupied or physical in ignored or eid in existing | source_ids:
            continue
        title = device.get('name_by_user') or device.get('name') or eid
        # Whole panels, production and savings must not be counted as appliance consumption.
        description = normalized(title + ' ' + eid + ' ' + attrs.get('friendly_name', ''))
        if any(word in description.split() for word in ('medidor', 'meter', 'solar', 'production', 'producao', 'produzida', 'export', 'exportacao', 'generation', 'geracao', 'economizada', 'saved')):
            continue
        candidates.setdefault(physical, []).append({'stat_consumption': eid, 'name': title})
        metadata = stats.get(eid, {})
        unit = metadata.get('statistics_unit_of_measurement', metadata.get('unit_of_measurement'))
        if (attrs.get('state_class') not in {'total', 'total_increasing'}
                or attrs.get('unit_of_measurement') not in ENERGY_UNITS
                or not metadata.get('has_sum') or unit not in ENERGY_UNITS):
            pending[eid] = 'Aguardando estatisticas de consumo compativeis com Energia'
            continue
        try:
            value = float(states.get(eid, {}).get('state'))
            if not math.isfinite(value) or value < 0:
                raise ValueError
        except (ValueError, TypeError):
            pending[eid] = 'Leitura de consumo indisponivel'
            continue
    additions, managed = [], {}
    for physical, rows in sorted(candidates.items()):
        if len(rows) != 1:
            for row in rows:
                pending[row['stat_consumption']] = 'Multiplos contadores de energia; selecao manual necessaria'
            continue
        row = rows[0]
        if row['stat_consumption'] in pending:
            continue
        additions.append(row)
        managed[row['stat_consumption']] = physical
    return {'additions': additions, 'managed': managed, 'pending': pending}


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def sync(client, data_dir):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / 'energy-dashboard-sync.json'
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {}
    entities = client.call('config/entity_registry/list')
    devices = client.call('config/device_registry/list')
    states = {s['entity_id']: s for s in client.call('get_states')}
    statistics = client.call('recorder/list_statistic_ids', statistic_type='sum')
    prefs = client.call('energy/get_prefs')
    current = {r['stat_consumption'] for r in prefs.get('device_consumption', [])}
    managed = state.get('managed', {})
    ignored = set(state.get('ignored_devices', []))
    for eid, physical in managed.items():
        if eid not in current:
            ignored.add(physical)  # Respect removal in HA's own Energy settings.
    result = plan(prefs, entities, devices, states, statistics, ignored)
    added = []
    if result['additions']:
        # Only the device list is saved. Abort on concurrent edits instead of replacing them.
        latest = client.call('energy/get_prefs')
        if latest != prefs:
            state.update(updated_at=time.time(), status='Configuracao mudou durante a leitura; nova tentativa no proximo ciclo')
            write(path, state)
            return state
        backup = data_dir / ('energy-prefs-before-' + str(time.time_ns()) + '.json')
        write(backup, prefs)
        requested = prefs.get('device_consumption', []) + result['additions']
        saved = client.call('energy/save_prefs', device_consumption=requested)
        confirmed = {r['stat_consumption'] for r in saved.get('device_consumption', [])}
        added = [r for r in result['additions'] if r['stat_consumption'] in confirmed]
        managed.update({r['stat_consumption']: result['managed'][r['stat_consumption']] for r in added})
    state.update(updated_at=time.time(), managed=managed, ignored_devices=sorted(ignored),
                 added=added, pending=result['pending'], status='Sincronizacao com Energia do HA concluida')
    write(path, state)
    return state
