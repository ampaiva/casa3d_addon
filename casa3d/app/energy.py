"""Discover electrical measurements and resolve only evidenced circuit assignments."""
MEASUREMENTS = {'energy', 'power', 'current', 'voltage'}


def discover(controls, registry, normalize, learning=None):
    devices = {d['id']: d for d in registry['devices'] if not d.get('disabled_by')}
    entities = {e['entity_id']: e for e in registry['entities'] if not e.get('disabled_by') and not e.get('hidden_by')}
    points = {key: (floor, point) for floor, positions in controls.get('device_positions', {}).items()
              for key, point in positions.items()}
    infrastructure = controls.get('infrastructure', {})
    panels = infrastructure.get('panels', {})
    feeds = infrastructure.get('feeds', {})
    result = []
    for device_id, device in devices.items():
        readings = []
        for entity_id, entry in entities.items():
            cls = entry.get('device_class') or entry.get('original_device_class')
            if entry.get('device_id') == device_id and cls in MEASUREMENTS:
                readings.append({'entity': entity_id, 'kind': cls})
        if not readings:
            continue
        location = next(((key, floor, point) for key, (floor, point) in points.items()
                         if point.get('ha_device_id') == device_id), None)
        if not location:
            continue
        key, floor, point = location
        # A feed explicitly registered for any entity of this same device is evidence.
        device_keys = {key} | {normalize(eid) for eid, e in entities.items() if e.get('device_id') == device_id}
        candidates = []
        for feed_key, feed in feeds.items():
            if feed_key in device_keys and feed.get('panel') in panels:
                panel = panels[feed['panel']]
                circuit = next((c for c in panel.get('circuits', []) if str(c['number']) == str(feed.get('circuit'))), None)
                candidates.append({'panel': feed['panel'], 'circuit': feed.get('circuit', ''),
                                   'phase': feed.get('phase') or (circuit or {}).get('phase', ''),
                                   'source': 'Vinculo eletrico cadastrado'})
        # Also recognize a circuit whose registered measurement belongs to this device.
        for panel_id, panel in panels.items():
            for circuit in panel.get('circuits', []):
                if entities.get(circuit.get('entity'), {}).get('device_id') == device_id:
                    candidates.append({'panel': panel_id, 'circuit': circuit['number'],
                                       'phase': circuit.get('phase', ''), 'source': 'Leitura cadastrada no circuito'})
        unique = {(c['panel'], str(c['circuit']), c['phase']) for c in candidates}
        assignment = candidates[0] if len(unique) == 1 else {}
        result.append({'device_id': device_id, 'key': key, 'title': point.get('title', key),
                       'area': point.get('ha_area_name', ''), 'floor': floor, 'readings': readings,
                       'assignment': assignment,
                       'learning': (learning or {}).get('devices', {}).get(device_id, {'status': 'Comparacao de consumo aguardando dados'}),
                       'assignment_status': 'Identificado' if assignment else
                       ('Vinculos conflitantes' if candidates else 'Quadro, circuito e fase a identificar')})
    return result
