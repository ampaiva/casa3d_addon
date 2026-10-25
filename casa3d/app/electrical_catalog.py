"""Import the existing electrical catalogue without inventing numbered breakers."""
import json
from pathlib import Path
import re
import tempfile
import threading
import unicodedata

LOCK = threading.Lock()


def normalized(text):
    value = ''.join(c for c in unicodedata.normalize('NFKD', str(text).casefold()) if not unicodedata.combining(c))
    words = re.sub(r'[^a-z0-9 ]', ' ', value).split()
    return ' '.join(w for w in words if w not in {'de', 'do', 'da', 'dos', 'das'})


def electrical_view(dashboard):
    return next((v for v in dashboard.get('views', []) if v.get('path') == 'eletrica'), {})


def walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def parse(dashboard):
    rows, notes = [], []
    for card in walk(electrical_view(dashboard)):
        if card.get('type') != 'markdown' or normalized(card.get('title', '')) != 'circuitos catalogados':
            continue
        table_seen = False
        for line in card.get('content', '').splitlines():
            if not line.strip().startswith('|'):
                if table_seen and line.strip():
                    notes.append(line.strip())
                continue
            cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
            if len(cells) != 5 or normalized(cells[0]) == 'equipamento' or set(cells[0]) <= {'-', ':', ' '}:
                continue
            table_seen = True
            phases = [p.strip().upper() for p in cells[2].split('+')]
            if not cells[0] or not cells[1] or not phases or any(p not in {'A', 'B', 'C'} for p in phases):
                continue
            rows.append({'id': normalized(cells[0]), 'equipment': cells[0], 'panel_label': cells[1],
                         'channels': phases, 'connection': cells[3], 'confidence': cells[4],
                         'source': 'Catalogo eletrico existente'})
    return rows, notes


def meter_references(dashboard):
    """Explicit per-phase entities in the user's existing dashboard define scope."""
    refs = {}
    for card in walk(electrical_view(dashboard)):
        title = card.get('title', '')
        if card.get('type') != 'entities' or not normalized(title).startswith('quadro '):
            continue
        for entry in card.get('entities', []):
            if not isinstance(entry, dict) or not entry.get('entity'):
                continue
            match = re.fullmatch(r'fase ([abc])', normalized(entry.get('name', '')))
            if match:
                channel = match[1].upper()
                slot = normalized(title) + ':' + channel
                refs[slot] = {'entity': entry['entity'], 'channel': channel, 'panel_label': title}
    return refs


def device_labels(controls, registry):
    devices = {d['id']: d for d in registry['devices'] if not d.get('disabled_by')}
    areas = {a.get('id', a.get('area_id')): a for a in registry.get('areas', [])}
    labels = {key: {normalized(d.get('name') or ''), normalized(d.get('name_by_user') or '')} - {''}
              for key, d in devices.items()}
    entities = {e['entity_id']: e for e in registry['entities'] if e.get('device_id') in devices}
    for value in controls.values():
        if isinstance(value, list):
            for control in value:
                device_id = entities.get(control.get('entity'), {}).get('device_id')
                if device_id:
                    labels[device_id].add(normalized(control.get('title', '')))
    for points in controls.get('device_positions', {}).values():
        for point in points.values():
            if point.get('ha_device_id') in labels:
                labels[point['ha_device_id']].add(normalized(point.get('title', '')))
    # Appliance terminology is matched narrowly, with climate devices qualified by HA area.
    for device_id, names in labels.items():
        if 'lavadora' in names:
            names.add('maquina lavar roupa')
        climate = [e for e in entities.values() if e.get('device_id') == device_id and e['entity_id'].startswith('climate.')]
        if climate:
            area = areas.get(devices[device_id].get('area_id'), {}).get('name', '')
            if area:
                name = normalized(area)
                names.add('ar condicionado ' + name)
                if name.startswith('quarto '):
                    names.add('ar condicionado ' + name[len('quarto '):])
    return labels


def resolve(dashboard, controls, registry, data_dir=None):
    rows, notes = parse(dashboard)
    labels = device_labels(controls, registry)
    with LOCK:
        path = Path(data_dir) / 'electrical-catalog-bindings.json' if data_dir else None
        try:
            saved = json.loads(path.read_text()) if path else {}
        except (OSError, ValueError):
            saved = {}
        original = dict(saved)
        for row in rows:
            if row['id'] in saved:
                match = saved[row['id']]
                row['device_id'] = match if match in labels else None
            else:
                matches = [key for key, names in labels.items() if row['id'] in names]
                row['device_id'] = matches[0] if len(matches) == 1 else None
                if row['device_id']:
                    saved[row['id']] = row['device_id']
            row['status'] = 'Vinculado' if row['device_id'] else 'Dispositivo do catalogo a vincular'
        if path and saved != original:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, suffix='.tmp') as handle:
                json.dump(saved, handle, ensure_ascii=False, indent=2)
            Path(handle.name).replace(path)
    return {'rows': rows, 'notes': notes}
