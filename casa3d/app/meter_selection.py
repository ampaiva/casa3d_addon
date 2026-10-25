"""Choose active measurement sources, preserving missing expected channels."""
from .energy_learning import watts, phase_channel
from .electrical_catalog import meter_references, normalized


def physical_key(device):
    ids = [('tuya' if domain in {'tuya', 'localtuya'} else str(domain), str(identifier)) for domain, identifier in device.get('identifiers', [])
           if domain in {'tuya', 'localtuya', 'zha'}]
    return sorted(ids)[0] if ids else ('ha', device['id'])


def select(entities, devices, states, dashboard):
    devices = {d['id']: d for d in devices if not d.get('disabled_by')}
    candidates = {}
    for entry in entities:
        entity = entry['entity_id']
        device = devices.get(entry.get('device_id'))
        state = states.get(entity, {})
        attributes = state.get('attributes', {})
        if (not device or entry.get('disabled_by') or entry.get('hidden_by') or not entity.startswith('sensor.')
                or (entry.get('device_class') or entry.get('original_device_class') or attributes.get('device_class')) != 'power'):
            continue
        title = device.get('name_by_user') or device.get('name') or entity
        channel = phase_channel(entity, attributes.get('friendly_name', ''))
        if not channel or not any(word in normalized(title) for word in ('medidor', 'meter')):
            continue
        candidates[entity] = {'entity': entity, 'channel': channel, 'title': title.strip(),
                              'device_id': device['id'], 'physical_key': physical_key(device),
                              'available': watts(state.get('state'), attributes.get('unit_of_measurement')) is not None}
    refs = meter_references(dashboard)
    if not refs:
        # Without an explicit dashboard, retain every observed device/channel as expected.
        for entity, entry in candidates.items():
            slot = str(entry['physical_key']) + ':' + entry['channel']
            refs.setdefault(slot, {'entity': entity, 'channel': entry['channel'], 'panel_label': entry['title']})
    selected, excluded, missing = {}, {}, []
    for slot, ref in refs.items():
        original = candidates.get(ref['entity'])
        alternatives = [entry for entry in candidates.values()
                        if original and entry['physical_key'] == original['physical_key'] and entry['channel'] == ref['channel']]
        eligible = [entry for entry in alternatives if entry['available']]
        preferred = original if original and original['available'] else next(iter(sorted(eligible, key=lambda e: e['entity'])), None)
        if preferred:
            selected[preferred['entity']] = {k: preferred[k] for k in ('channel', 'title', 'device_id')}
            selected[preferred['entity']].update(slot=slot, panel_label=ref['panel_label'])
        else:
            missing.append({'slot': slot, **ref, 'reason': 'Canal de referencia sem leitura valida'})
    for entity, candidate in candidates.items():
        if entity not in selected:
            excluded[entity] = 'Leitura indisponivel ou unidade invalida' if not candidate['available'] else 'Outra representacao do canal ou fora dos medidores de referencia'
    return {'meters': selected, 'excluded': excluded, 'missing': missing, 'expected_channels': len(refs)}
