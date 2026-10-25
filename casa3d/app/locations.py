"""Bind HA registry identifiers to floor images and room rectangles."""
import math
import hashlib

FLOORS = {'terreo', 'superior'}


def validate(mapping):
    if not isinstance(mapping, dict):
        raise ValueError('Mapeamento invalido')
    for floor in mapping.get('floors', {}).values():
        if floor not in FLOORS and floor != '':
            raise ValueError('Planta invalida')
    for room in mapping.get('rooms', {}).values():
        if room.get('floor', '') not in FLOORS | {''}:
            raise ValueError('Planta invalida')
        bounds = [float(room.get(key, default)) for key, default in
                  [('left', 40), ('top', 40), ('width', 20), ('height', 20)]]
        if not all(math.isfinite(n) and 0 <= n <= 100 for n in bounds):
            raise ValueError('Limites do comodo devem estar entre 0 e 100')
        x, y, w, h = bounds
        if w <= 0 or h <= 0 or x + w > 100 or y + h > 100:
            raise ValueError('Regiao do comodo deve caber na planta')


def reconcile(data, registry, normalize):
    mapping = data.setdefault('location_mapping', {'floors': {}, 'rooms': {}})
    validate(mapping)
    areas = {a['id']: a for a in registry['areas']}
    devices = {d['id']: d for d in registry['devices']}
    entities = registry['entities']
    positions = data.setdefault('device_positions', {})
    pending = positions.setdefault('unlocated', {})
    # Relate legacy icons to registry device IDs before discovering new devices.
    entity_devices = {e['entity_id']: e.get('device_id') for e in entities}
    for points in positions.values():
        for key, point in points.items():
            matches = {device for entity, device in entity_devices.items()
                       if device and (entity == key or normalize(entity) == key)}
            if len(matches) == 1:
                point.setdefault('ha_device_id', matches.pop())
            if point.get('sensor_device_id'):
                point.setdefault('ha_device_id', point['sensor_device_id'])
    represented = {p.get('ha_device_id') for points in positions.values() for p in points.values()}
    for entry in entities:
        device_id = entry.get('device_id')
        device = devices.get(device_id, {})
        domain = entry['entity_id'].split('.')[0]
        cls = entry.get('device_class') or entry.get('original_device_class')
        if (not device_id or device_id in represented or not device or device.get('disabled_by')
                or entry.get('disabled_by') or entry.get('entity_category')
                or entry.get('hidden_by')):
            continue
        point = {'title': device.get('name_by_user') or device.get('name') or entry['entity_id'],
                 'ha_device_id': device_id, 'auto_discovered': True, 'left': '50%', 'top': '50%', 'position_pending': True}
        if domain == 'binary_sensor' and cls:
            point.update(sensor_device_id=device_id, device_class=cls)
        pending[entry['entity_id']] = point
        represented.add(device_id)
    moves = []
    for source, points in positions.items():
        for key, point in list(points.items()):
            device = devices.get(point.get('ha_device_id'))
            if not device or device.get('disabled_by'):
                if point.get('auto_discovered'):
                    moves.append((source, None, key, point))
                continue
            if point.get('auto_discovered'):
                active = [e for e in entities if e.get('device_id') == device['id']
                          and not e.get('disabled_by') and not e.get('hidden_by') and not e.get('entity_category')]
                if not active:
                    moves.append((source, None, key, point)); continue
                if not any(e['entity_id'] == key for e in active):
                    replacement = active[0]['entity_id']
                    if replacement not in points:
                        moves.append((source, None, key, point))
                        # Keep the stable device identity and manual coordinates through entity renames.
                        points[replacement] = point
                        key = replacement
                point['title'] = device.get('name_by_user') or device.get('name') or point.get('title', key)
            entry = next((e for e in entities if e['entity_id'] == key), {})
            area_id = entry.get('area_id') or device.get('area_id')
            area = areas.get(area_id, {})
            room = mapping.get('rooms', {}).get(area_id)
            target = ((room.get('floor') or mapping.get('floors', {}).get(area.get('floor_id')))
                      if room else None)
            target = target if target in FLOORS else 'unlocated'
            # Leave legacy manual placements intact until their room is linked.
            # Once managed, an unlinked/missing HA area returns the icon to the queue.
            if target == 'unlocated' and source != 'unlocated' and not point.get('location_managed') and not point.get('position_pending'):
                continue
            point['location_managed'] = True
            previous = point.get('ha_area_id')
            # Adopt existing manually placed icons when the first binding agrees with their floor.
            changed = source != target or ('ha_area_id' in point and previous != area_id)
            if room and target != 'unlocated':
                x, y = float(str(point.get('left', '50%')).rstrip('%')), float(str(point.get('top', '50%')).rstrip('%'))
                inside = (float(room.get('left', 40)) <= x <= float(room.get('left', 40)) + float(room.get('width', 20))
                          and float(room.get('top', 40)) <= y <= float(room.get('top', 40)) + float(room.get('height', 20)))
                if point.get('position_pending') or ('ha_area_id' not in point and not inside):
                    changed = True
            point.update(ha_area_id=area_id, ha_area_name=area.get('name', ''),
                         location_reason='' if target != 'unlocated' else
                         ('Comodo sem vinculo com a planta' if area_id else 'Sem comodo no Home Assistant'))
            if changed:
                point['position_pending'] = True
                if target != 'unlocated':
                    # New generic devices get stable positions inside the room instead of stacking.
                    digest = hashlib.sha256(device['id'].encode()).digest()
                    fx, fy = ((0.25 + digest[i] / 510) if point.get('auto_discovered') and not point.get('sensor_device_id') else 0.5 for i in (0, 1))
                    point['left'] = f"{float(room.get('left', 40)) + float(room.get('width', 20)) * fx:.2f}%"
                    point['top'] = f"{float(room.get('top', 40)) + float(room.get('height', 20)) * fy:.2f}%"
                moves.append((source, target, key, point))
    for source, target, key, point in moves:
        del positions[source][key]
        if target is not None:
            positions.setdefault(target, {})[key] = point
    return data
