"""One-way room sync. HA manual assignments always take precedence."""
import json
import threading
import time
import unicodedata

from .tuya_names import TuyaReader, ENDPOINTS

LOCK = threading.Lock()


def normalized(name):
    words = ''.join(c for c in unicodedata.normalize('NFKD', name.casefold()) if not unicodedata.combining(c))
    words = ' '.join(w for w in words.split() if w not in {'de', 'do', 'da', 'dos', 'das'})
    return {'sala estar': 'sala'}.get(words, words)


def read_state(data_dir):
    try:
        return json.loads((data_dir / 'tuya-room-sync.json').read_text())
    except (OSError, ValueError):
        return {'bindings': {}, 'managed': {}, 'rooms': [], 'updated_at': 0}


def write_state(data_dir, state):
    path = data_dir / 'tuya-room-sync.json'
    data_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temporary.replace(path)


def cloud_rooms(config_dir, reader_factory=TuyaReader):
    entries = json.loads((config_dir / '.storage/core.config_entries').read_text())['data']['entries']
    accounts = [e['data'] for e in entries if e['domain'] == 'localtuya'
                and not e['data'].get('no_cloud') and e['data'].get('client_id')
                and e['data'].get('client_secret') and e['data'].get('region') in ENDPOINTS]
    if not accounts:
        raise RuntimeError('Conta Tuya com acesso a comodos nao encontrada')
    rooms = {}
    for account in accounts:
        reader = reader_factory(account)
        reader.token = reader.get('/v1.0/token?grant_type=1')['access_token']
        uid = account.get('user_id') or account.get('uid')
        if not uid:
            continue
        for home in reader.get('/v1.0/users/' + uid + '/homes'):
            home_id = str(home['home_id'])
            records = reader.get('/v1.0/homes/' + home_id + '/rooms')
            if isinstance(records, dict):
                records = records.get('rooms', [])
            for room in records:
                room_id = str(room['room_id'])
                members = reader.get('/v1.0/homes/' + home_id + '/rooms/' + room_id + '/devices')
                if isinstance(members, dict):
                    members = members.get('devices', [])
                key = account['region'] + ':' + home_id + ':' + room_id
                rooms[key] = {'key': key, 'name': room['name'].strip(),
                              'devices': [str(d.get('id') or d.get('device_id')) for d in members]}
    if not rooms:
        raise RuntimeError('Nenhum comodo Tuya encontrado')
    return list(rooms.values())


def device_index(devices):
    index = {}
    for device in devices:
        if device.get('disabled_by'):
            continue
        for domain, identifier in device.get('identifiers', []):
            if domain in {'tuya', 'localtuya'}:
                index.setdefault(identifier, []).append(device)
    return index


def resolve_area(room, areas, bindings, explicit):
    """None means create; empty string means intentionally ignored or ambiguous."""
    if room['key'] in explicit:
        wanted = explicit[room['key']]
        return wanted if wanted and any(a['area_id'] == wanted for a in areas) else ''
    wanted = bindings.get(room['key'])
    if wanted and any(a['area_id'] == wanted for a in areas):
        return wanted
    matches = [a['area_id'] for a in areas if normalized(a['name']) == normalized(room['name'])]
    if len(matches) > 1:
        return ''
    return matches[0] if matches else None


def should_assign(device, target, managed):
    previous = managed.get(device['id'])
    current = device.get('area_id')
    if previous:
        if previous.get('manual_override') or current != previous.get('area_id'):
            previous['manual_override'] = True
            return False
        return current != target
    return not current and current != target


def sync(config_dir, data_dir, client, explicit=None, rooms=None):
    """Only HA's supported registry API writes its registries; never edit .storage."""
    with LOCK:
        state = read_state(data_dir)
        rooms = cloud_rooms(config_dir) if rooms is None else rooms
        areas = client.call('config/area_registry/list')
        devices = client.call('config/device_registry/list')
        index = device_index(devices)
        assigned = 0
        output = []
        # Multiple homes assigning the same device differently require explicit resolution.
        memberships = {}
        for room in rooms:
            for device_id in room['devices']:
                memberships.setdefault(device_id, set()).add(room['key'])
        for room in rooms:
            target = resolve_area(room, areas, state['bindings'], explicit or {})
            if target is None:
                created = client.call('config/area_registry/create', name=room['name'])
                areas.append(created)
                target = created['area_id']
                # A room can inherit an evidenced floor, but not an invented floor.
                floors = {a.get('floor_id') for d in room['devices'] for device in index.get(d, [])
                          for a in areas if a['area_id'] == device.get('area_id') and a.get('floor_id')}
                if len(floors) == 1:
                    client.call('config/area_registry/update', area_id=target, floor_id=floors.pop())
                state['bindings'][room['key']] = target
                write_state(data_dir, state)
            if not target:
                output.append({**room, 'area_id': '', 'status': 'Vinculo pendente ou ignorado'})
                continue
            state['bindings'][room['key']] = target
            for external_id in room['devices']:
                if len(memberships[external_id]) != 1:
                    continue
                for device in index.get(external_id, []):
                    if should_assign(device, target, state['managed']):
                        client.call('config/device_registry/update', device_id=device['id'], area_id=target)
                        state['managed'][device['id']] = {'area_id': target, 'room': room['key']}
                        device['area_id'] = target
                        assigned += 1
                        write_state(data_dir, state)
            output.append({k: v for k, v in {**room, 'area_id': target, 'status': 'Sincronizado'}.items() if k != 'devices'})
        state.update(rooms=output, updated_at=time.time(), assigned=assigned)
        state.pop('error', None)
        write_state(data_dir, state)
        return state
