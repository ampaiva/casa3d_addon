"""Passive comparison of load changes; results are evidence, never wiring facts."""
from bisect import bisect_right
from datetime import datetime
import math
import re


def phase_channel(entity, name=''):
    match = re.search(r'(?:phase[_ ]|fase[_ ](?:da[_ ]fase[_ ])?)([abc])(?:[_ ]|$)', entity + ' ' + name, re.I)
    return match[1].upper() if match else None


def watts(value, unit):
    if unit not in {'W', 'kW'}:
        return None
    try:
        result = float(value) * (1000 if unit == 'kW' else 1)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def series(history, unit, divisor=1, before=None):
    points = []
    for state in history:
        value = watts(state.get('state'), unit)
        stamp = state.get('last_changed') or state.get('last_updated')
        if not stamp:
            continue
        try:
            timestamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
            if value is not None and before is not None and timestamp < before:
                value /= divisor
            points.append((timestamp, value))
        except (ValueError, TypeError):
            continue
    return sorted(points, key=lambda p: p[0])


def at(points, timestamp, max_age=None):
    i = bisect_right(points, timestamp, key=lambda p: p[0]) - 1
    # HA records state changes: a held value remains valid until the next state,
    # including an explicit unavailable marker. Sparse history is not a data outage.
    return points[i][1] if i >= 0 and (max_age is None or timestamp - points[i][0] <= max_age) else None


def compare(device, meters, missing_channels=None):
    """Demand repeated signed transitions and a unique winner across all meters."""
    events = []
    last = -float('inf')
    for (previous_t, before), (timestamp, after) in zip(device, device[1:]):
        if before is None or after is None:
            continue
        delta = after - before
        if abs(delta) < 100 or timestamp - last < 180:
            continue
        # Never extrapolate a just-started load into a future observation window.
        if timestamp + 90 > device[-1][0]:
            continue
        # Ignore transient spikes and windows without fresh readings.
        stable = at(device, timestamp + 90, max_age=float('inf'))
        if stable is None or abs(stable - after) > max(40, abs(delta) * .15):
            continue
        events.append((timestamp, delta))
        last = timestamp
    scores = []
    for entity, points in meters.items():
        matches = []
        compared = 0
        for timestamp, delta in events:
            before = at(points, timestamp - 60)
            after = at(points, timestamp + 90)
            if before is None or after is None:
                continue
            compared += 1
            observed = after - before
            if observed * delta > 0 and abs(observed - delta) <= max(50, abs(delta) * .25):
                matches.append(delta)
        positive, negative = sum(x > 0 for x in matches), sum(x < 0 for x in matches)
        score = len(matches) / compared if compared else 0
        scores.append({'entity': entity, 'score': round(score, 3), 'matches': len(matches),
                       'compared': compared, 'rises': positive, 'falls': negative})
    scores.sort(key=lambda s: (s['score'], s['matches']), reverse=True)
    if not scores:
        return {'status': 'Sem medidores de referencia ativos', 'candidate': None, 'events': len(events), 'scores': []}
    best = scores[0]
    runner = scores[1]['score'] if len(scores) > 1 else 0
    coverage = [score for score in scores if score['compared'] < 6]
    if missing_channels:
        reason, status = 'missing_channels', 'Medidores de referencia incompletos: %s canal(is) sem leitura' % len(missing_channels)
    elif len(events) < 6:
        reason, status = 'insufficient_events', 'Poucas variacoes da carga: %s (minimo 6)' % len(events)
    elif coverage:
        reason, status = 'insufficient_coverage', 'Historico insuficiente em %s canal(is) de referencia' % len(coverage)
    elif best['score'] >= .8 and best['score'] - runner < .25:
        reason, status = 'ambiguous', 'Canais com sinais semelhantes; alimentacao ainda ambigua'
    elif best['matches'] < 6 or best['rises'] < 2 or best['falls'] < 2 or best['score'] < .8:
        reason, status = 'low_agreement', 'Variacoes sem coincidencias suficientes nos dois sentidos'
    else:
        reason, status = 'matched', 'Associacao provavel'
    return {'status': status, 'reason': reason, 'candidate': best if reason == 'matched' else None,
            'events': len(events), 'scores': scores}
