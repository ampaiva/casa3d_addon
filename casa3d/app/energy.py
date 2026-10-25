"""Discover electrical measurements and resolve only evidenced circuit assignments."""
MEASUREMENTS = {'energy', 'power', 'current', 'voltage'}


def discover(controls, registry, normalize, learning=None, catalog=None):
    devices = {d['id']: d for d in registry['devices'] if not d.get('disabled_by')}
    entities = {e['entity_id']: e for e in registry['entities'] if not e.get('disabled_by') and not e.get('hidden_by')}
    points = {key: (floor, point) for floor, positions in controls.get('device_positions', {}).items()
              for key, point in positions.items()}
    infrastructure = controls.get('infrastructure', {})
    panels = infrastructure.get('panels', {})
    feeds = infrastructure.get('feeds', {})
    areas = {a.get('id', a.get('area_id')): a for a in registry.get('areas', [])}
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
        catalog_rows = [row for row in (catalog or {}).get('rows', []) if row.get('device_id') == device_id]
        catalog_values = {(row['panel_label'], tuple(row['channels'])) for row in catalog_rows}
        catalog_assignment = catalog_rows[0] if len(catalog_values) == 1 else None
        if assignment:
            assignment_status = 'Identificado'
        elif candidates:
            assignment_status = 'Vinculos conflitantes'
        elif len(catalog_values) > 1:
            assignment_status = 'Vinculos conflitantes no catalogo'
        elif catalog_assignment:
            assignment_status = 'Quadro %s · Fases %s · Circuito a identificar' % (catalog_assignment['panel_label'], ' + '.join(catalog_assignment['channels']))
        else:
            assignment_status = 'Quadro, circuito e fase a identificar'
        result.append({'device_id': device_id, 'key': key, 'title': point.get('title', key),
                       'area': areas.get(device.get('area_id'), {}).get('name') or point.get('ha_area_name', ''), 'floor': floor, 'readings': readings,
                       'assignment': assignment, 'catalog_assignment': catalog_assignment,
                       'assignment_conflict': bool(candidates and not assignment) or len(catalog_values) > 1,
                       'learning': (learning or {}).get('devices', {}).get(device_id, {'status': (learning or {}).get('status') or 'Comparacao de consumo aguardando dados'}),
                       'assignment_status': assignment_status})
    return result
