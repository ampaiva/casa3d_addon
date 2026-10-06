import argparse
import json
import mimetypes
import os
import re
import subprocess
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
CONTROLS = ROOT / "casa3d-controls.json"
ASSETS = ROOT / "assets"
FLOOR_IMAGES = {
    "terreo": "casa_3d_terreo-v18.webp",
    "superior": "casa_3d_superior-original.webp",
}
OPTIONS_PATH = Path(os.getenv("CASA3D_OPTIONS_PATH", "/data/options.json"))
DEVICE_OVERRIDES = {
    "switch.luz_do_jardim": {"title": "Luz do Jardim", "model": "Girier"},
}


def pct(value):
    return float(str(value).rstrip("%"))


def pct_text(value):
    return f"{max(0, min(100, value)):.2f}%"


def normalize_device(entity):
    key = re.sub(r"_switch_[1-4](?:_2)?$", "", entity)
    key = re.sub(r"_socket_1$", "", key)
    return key


def channel_number(entity):
    match = re.search(r"_switch_([1-4])(?:_2)?$", entity)
    if match:
        return int(match.group(1))
    return 1


def device_title(group):
    title = group[0].get("title", "Dispositivo")
    return re.sub(r"\s+[1-4]$", "", title)


def ensure_device_positions(data):
    positions = data.setdefault("device_positions", {})
    for floor, controls in data.items():
        if floor == "device_positions" or not isinstance(controls, list):
            continue

        floor_positions = positions.setdefault(floor, {})
        groups = defaultdict(list)
        for control in controls:
            entity = control.get("entity", "")
            if entity.startswith("climate."):
                continue
            groups[normalize_device(entity)].append(control)

        for key, group in groups.items():
            channels = sorted({channel_number(item.get("entity", "")) for item in group})
            if key in floor_positions:
                override = DEVICE_OVERRIDES.get(key, {})
                floor_positions[key].update(
                    {field: value for field, value in override.items() if field not in floor_positions[key] or key in DEVICE_OVERRIDES}
                )
                if len(channels) > 1:
                    if "model" in override:
                        floor_positions[key]["model"] = override["model"]
                    elif floor_positions[key].get("model") == "Girier":
                        floor_positions[key]["model"] = "Modulo multicanal"
                    else:
                        floor_positions[key].setdefault("model", "Modulo multicanal")
                    floor_positions[key]["channels"] = len(channels)
                continue
            left = sum(pct(item["left"]) for item in group) / len(group)
            top = sum(pct(item["top"]) for item in group) / len(group)
            override = DEVICE_OVERRIDES.get(key, {})
            floor_positions[key] = {
                "title": override.get("title", device_title(group)),
                "left": pct_text(left),
                "top": pct_text(top),
            }
            if len(channels) > 1:
                floor_positions[key]["model"] = override.get("model", "Modulo multicanal")
                floor_positions[key]["channels"] = len(channels)
    return data


def load_controls():
    return ensure_device_positions(json.loads(CONTROLS.read_text()))


def floor_images():
    images = dict(FLOOR_IMAGES)
    if OPTIONS_PATH.exists():
        try:
            options = json.loads(OPTIONS_PATH.read_text())
        except json.JSONDecodeError:
            options = {}
        images["terreo"] = options.get("floor_ground_image") or images["terreo"]
        images["superior"] = options.get("floor_upper_image") or images["superior"]
    return images


def save_controls(data):
    ensure_device_positions(data)
    CONTROLS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def rebuild_dashboard():
    source = Path("/tmp/lovelace-casa3d-latest.json")
    if not source.exists():
        return
    subprocess.run(
        ["python3", str(ROOT / "scripts" / "build_casa3d_layers.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


HTML = r"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Casa 3D - Editor de posicoes</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f5f2;
      --panel: #ffffff;
      --text: #171717;
      --muted: #60666c;
      --line: #d7d9d6;
      --load: #0f8db3;
      --device: #d86b1f;
      --selected: #111827;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 0 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.92);
      position: sticky;
      top: 0;
      z-index: 5;
    }
    h1 {
      font-size: 18px;
      line-height: 1;
      margin: 0;
      font-weight: 680;
    }
    button, select, input {
      font: inherit;
    }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    button, select {
      min-height: 36px;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 6px;
      padding: 0 10px;
      color: var(--text);
    }
    button {
      cursor: pointer;
      font-weight: 620;
    }
    button.primary {
      background: #161a1d;
      border-color: #161a1d;
      color: #fff;
    }
    main {
      height: calc(100vh - 56px);
      display: grid;
      grid-template-columns: minmax(0, 1fr) 340px;
    }
    .stageWrap {
      min-width: 0;
      overflow: auto;
      padding: 20px;
    }
    .stage {
      position: relative;
      width: min(1400px, 100%);
      margin: 0 auto;
      border: 1px solid var(--line);
      background: #ececea;
      user-select: none;
      touch-action: none;
    }
    .stage img {
      display: block;
      width: 100%;
      height: auto;
      pointer-events: none;
    }
    .marker {
      position: absolute;
      left: var(--x);
      top: var(--y);
      transform: translate(-50%, -50%);
      width: 30px;
      height: 30px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      color: #fff;
      border: 2px solid rgba(255, 255, 255, 0.92);
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.42);
      cursor: grab;
      z-index: 2;
    }
    .marker:active { cursor: grabbing; }
    .marker.load { background: var(--load); }
    .marker.device {
      background: var(--device);
      border-radius: 8px;
      z-index: 3;
    }
    .device-symbol {
      width: 13px;
      height: 13px;
      border: 2px solid rgba(255, 255, 255, 0.96);
      border-radius: 3px;
      display: block;
      box-shadow: inset 0 0 0 2px rgba(255, 255, 255, 0.18);
      pointer-events: none;
    }
    .marker.module {
      width: auto;
      min-width: 46px;
      height: 34px;
      border-radius: 10px;
      padding: 0;
      display: flex;
      gap: 3px;
      background: transparent;
      border: 0;
      box-shadow: none;
      overflow: visible;
    }
    .marker.module.generic-module {
      width: 34px;
      min-width: 34px;
      height: 34px;
      border-radius: 9px;
      display: grid;
      place-items: center;
      background: var(--device);
      border: 2px solid rgba(255, 255, 255, 0.96);
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.36);
      color: #fff;
      font-size: 13px;
      font-weight: 800;
    }
    .marker.module.generic-module .module-count {
      pointer-events: none;
    }
    .marker.module.generic-module .channel {
      position: absolute;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      border: 1px solid rgba(255, 255, 255, 0.95);
      background: rgba(255, 255, 255, 0.5);
      box-shadow: none;
      color: transparent;
      padding: 0;
    }
    .generic-module.channels-2 .channel[data-channel="1"] { left: 33%; top: 84%; }
    .generic-module.channels-2 .channel[data-channel="2"] { left: 67%; top: 84%; }
    .generic-module.channels-3 .channel[data-channel="1"] { left: 25%; top: 84%; }
    .generic-module.channels-3 .channel[data-channel="2"] { left: 50%; top: 84%; }
    .generic-module.channels-3 .channel[data-channel="3"] { left: 75%; top: 84%; }
    .generic-module.channels-4 .channel[data-channel="1"] { left: 20%; top: 84%; }
    .generic-module.channels-4 .channel[data-channel="2"] { left: 40%; top: 84%; }
    .generic-module.channels-4 .channel[data-channel="3"] { left: 60%; top: 84%; }
    .generic-module.channels-4 .channel[data-channel="4"] { left: 80%; top: 84%; }
    .marker.module.device-image {
      --photo-w: 28px;
      --photo-h: 24px;
      width: var(--photo-w);
      height: var(--photo-h);
      display: block;
      background: transparent;
      z-index: 4;
      transition: width 140ms ease, height 140ms ease, filter 140ms ease;
    }
    .marker.module.device-image.expanded {
      --photo-w: 144px;
      --photo-h: 124px;
      z-index: 20;
      cursor: grabbing;
    }
    .marker.module.breaker-image {
      --photo-w: 18px;
      --photo-h: 29px;
    }
    .marker.module.breaker-image.expanded {
      --photo-w: 86px;
      --photo-h: 136px;
    }
    .device-photo {
      width: var(--photo-w);
      height: auto;
      display: block;
      border-radius: 8px;
      filter: drop-shadow(0 2px 5px rgba(0, 0, 0, 0.34));
      pointer-events: none;
      transition: width 140ms ease;
    }
    .channel {
      width: 30px;
      height: 34px;
      border-radius: 9px;
      display: grid;
      place-items: center;
      background: var(--device);
      color: #fff;
      border: 2px solid rgba(255, 255, 255, 0.96);
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.36);
      font-size: 12px;
      font-weight: 800;
      line-height: 1;
    }
    .device-image .channel {
      position: absolute;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      border: 2px solid #fff;
      background: #e46f18;
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.42);
      color: transparent;
      padding: 0;
    }
    .device-image.expanded .channel {
      width: 13px;
      height: 13px;
    }
    .channel-label {
      position: absolute;
      display: none;
      width: 1px;
      height: 1px;
      overflow: hidden;
    }
    .channel-list {
      position: absolute;
      display: none;
      left: 50%;
      top: calc(100% + 5px);
      transform: translateX(-50%);
      grid-template-columns: repeat(2, max-content);
      gap: 3px 7px;
      padding: 3px 6px;
      border-radius: 5px;
      background: rgba(13, 18, 23, 0.88);
      color: #fff;
      font-size: 10px;
      line-height: 13px;
      font-weight: 650;
      white-space: nowrap;
      pointer-events: none;
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.35);
    }
    .device-image.expanded .channel-list {
      display: grid;
    }
    .channel-list span {
      display: block;
    }
    .device-image.channels-1 .channel[data-channel="1"] { left: 50%; top: 81%; }
    .device-image.channels-2 .channel[data-channel="1"] { left: 43%; top: 81%; }
    .device-image.channels-2 .channel[data-channel="2"] { left: 57%; top: 81%; }
    .device-image.channels-3 .channel[data-channel="1"] { left: 37%; top: 81%; }
    .device-image.channels-3 .channel[data-channel="2"] { left: 50%; top: 81%; }
    .device-image.channels-3 .channel[data-channel="3"] { left: 63%; top: 81%; }
    .device-image.channels-4 .channel[data-channel="1"] { left: 30%; top: 81%; }
    .device-image.channels-4 .channel[data-channel="2"] { left: 43%; top: 81%; }
    .device-image.channels-4 .channel[data-channel="3"] { left: 56%; top: 81%; }
    .device-image.channels-4 .channel[data-channel="4"] { left: 69%; top: 81%; }
    .marker.module.selected .channel {
      outline: 3px solid var(--selected);
      outline-offset: 2px;
    }
    .marker.selected {
      outline: 3px solid var(--selected);
      outline-offset: 2px;
    }
    .marker span {
      font-size: 12px;
      font-weight: 800;
      line-height: 1;
    }
    .wire {
      position: absolute;
      inset: 0;
      pointer-events: none;
      width: 100%;
      height: 100%;
      z-index: 1;
    }
    .wire line {
      stroke: rgba(30, 64, 96, 0.34);
      stroke-width: 1.5;
      stroke-dasharray: 5 6;
    }
    .wire line.channel-wire {
      stroke: rgba(23, 85, 130, 0.56);
      stroke-width: 2;
      stroke-dasharray: 6 7;
    }
    aside {
      min-width: 0;
      border-left: 1px solid var(--line);
      background: var(--panel);
      overflow: auto;
      padding: 16px;
    }
    .legend {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-bottom: 14px;
    }
    .legend div {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      font-size: 13px;
      color: var(--muted);
    }
    .dot {
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      margin-right: 6px;
      background: var(--load);
    }
    .dot.device {
      border-radius: 3px;
      background: var(--device);
    }
    .details {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 14px;
    }
    .details h2 {
      margin: 0 0 6px;
      font-size: 16px;
    }
    .details p {
      margin: 0 0 10px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
    }
    .fields {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    label {
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 12px;
    }
    input {
      width: 100%;
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 8px;
      color: var(--text);
      background: #fff;
    }
    .list {
      display: grid;
      gap: 6px;
    }
    .row {
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 6px;
      padding: 9px 10px;
      text-align: left;
      display: grid;
      gap: 2px;
      cursor: pointer;
    }
    .row strong {
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .row span {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .row.selected {
      border-color: #1f2937;
      box-shadow: inset 3px 0 0 #1f2937;
    }
    .status {
      min-width: 150px;
      color: var(--muted);
      font-size: 13px;
    }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; height: auto; }
      aside { border-left: 0; border-top: 1px solid var(--line); }
      header { height: auto; min-height: 56px; align-items: flex-start; padding: 12px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Casa 3D - Editor de posicoes</h1>
    <div class="toolbar">
      <select id="floor"></select>
      <select id="mode">
        <option value="all">Dispositivos e cargas</option>
        <option value="device">Dispositivos fisicos</option>
        <option value="load">Lampadas e cargas</option>
      </select>
      <button id="save" class="primary">Salvar</button>
      <span id="status" class="status"></span>
    </div>
  </header>
  <main>
    <section class="stageWrap">
      <div id="stage" class="stage">
        <img id="map" alt="">
        <svg id="wires" class="wire"></svg>
      </div>
    </section>
    <aside>
      <div class="legend">
        <div><span class="dot device"></span>Switch fisico</div>
        <div><span class="dot"></span>Lampada ou carga</div>
      </div>
      <div class="details">
        <h2 id="selectedTitle">Selecione um ponto</h2>
        <p id="selectedMeta">Arraste no mapa ou ajuste as porcentagens.</p>
        <div class="fields">
          <label>Esquerda
            <input id="leftField" inputmode="decimal" disabled>
          </label>
          <label>Topo
            <input id="topField" inputmode="decimal" disabled>
          </label>
        </div>
      </div>
      <div id="list" class="list"></div>
    </aside>
  </main>
  <script>
    const stage = document.getElementById('stage');
    const map = document.getElementById('map');
    const wires = document.getElementById('wires');
    const floorSelect = document.getElementById('floor');
    const modeSelect = document.getElementById('mode');
    const list = document.getElementById('list');
    const status = document.getElementById('status');
    const selectedTitle = document.getElementById('selectedTitle');
    const selectedMeta = document.getElementById('selectedMeta');
    const leftField = document.getElementById('leftField');
    const topField = document.getElementById('topField');
    let data;
    let images;
    let selected = null;
    let dragging = null;

    const normalizeDevice = (entity) => entity
      .replace(/_switch_[1-4](_2)?$/, '')
      .replace(/_socket_1$/, '');
    const channelNumber = (entity) => {
      const match = String(entity || '').match(/_switch_([1-4])(_2)?$/);
      return match ? Number(match[1]) : 1;
    };
    const parsePct = (value) => Number(String(value).replace('%', ''));
    const fmtPct = (value) => `${Math.max(0, Math.min(100, value)).toFixed(2)}%`;

    function markerId(item) {
      return `${item.kind}:${item.key}:${item.index ?? ''}`;
    }

    function floor() {
      return floorSelect.value;
    }

    function mode() {
      return modeSelect.value;
    }

    function itemsForFloor() {
      const currentFloor = floor();
      const controls = data[currentFloor] || [];
      const devices = data.device_positions?.[currentFloor] || {};
      const items = [];

      if (mode() !== 'device') {
        controls.forEach((control, index) => {
          const key = normalizeDevice(control.entity || '');
          items.push({
            kind: 'load',
            key,
            channel: channelNumber(control.entity),
            index,
            title: control.title || control.entity,
            entity: control.entity,
            left: control.left,
            top: control.top
          });
        });
      }

      if (mode() !== 'load') {
        const channelsByDevice = controls.reduce((acc, control) => {
          const key = normalizeDevice(control.entity || '');
          const channel = channelNumber(control.entity);
          if (!acc[key]) acc[key] = new Set();
          acc[key].add(channel);
          return acc;
        }, {});
        const channelNamesByDevice = controls.reduce((acc, control) => {
          const key = normalizeDevice(control.entity || '');
          const channel = channelNumber(control.entity);
          if (!acc[key]) acc[key] = {};
          if (!acc[key][channel]) acc[key][channel] = [];
          const title = control.title || control.entity;
          if (!acc[key][channel].includes(title)) acc[key][channel].push(title);
          return acc;
        }, {});
        Object.entries(devices).forEach(([key, point]) => {
          const channels = Array.from(channelsByDevice[key] || [1]).sort((a, b) => a - b);
          const isSwitchDevice = key.startsWith('switch.');
          const isBreakerDevice = key === 'switch.tz3000_cayepv1a_ts011f';
          items.push({
            kind: 'device',
            key,
            title: point.title || key,
            entity: key,
            left: point.left,
            top: point.top,
            model: isBreakerDevice ? 'Disjuntor Zigbee' : (isSwitchDevice ? 'Girier' : (point.model || (channels.length > 1 ? 'Modulo multicanal' : 'Modulo Zigbee'))),
            channels,
            channelNames: channelNamesByDevice[key] || {}
          });
        });
      }

      return items;
    }

    function setItemPosition(item, left, top) {
      if (item.kind === 'load') {
        const control = data[floor()][item.index];
        control.left = fmtPct(left);
        control.top = fmtPct(top);
        item.left = control.left;
        item.top = control.top;
      } else {
        const point = data.device_positions[floor()][item.key];
        point.left = fmtPct(left);
        point.top = fmtPct(top);
        item.left = point.left;
        item.top = point.top;
      }
      status.textContent = 'Alteracoes nao salvas';
    }

    function stagePoint(event) {
      const rect = stage.getBoundingClientRect();
      return {
        left: ((event.clientX - rect.left) / rect.width) * 100,
        top: ((event.clientY - rect.top) / rect.height) * 100
      };
    }

    function selectItem(item) {
      selected = item;
      selectedTitle.textContent = item.title;
      selectedMeta.textContent = item.kind === 'device'
        ? `${item.model || 'Modulo Zigbee'} fisico${item.channels && item.channels.length > 1 ? ` · ${item.channels.length} canais` : ''}: ${item.entity}`
        : `Canal ${item.channel} alimenta: ${item.entity}`;
      leftField.disabled = false;
      topField.disabled = false;
      leftField.value = parsePct(item.left).toFixed(2);
      topField.value = parsePct(item.top).toFixed(2);
      render();
    }

    function updateSelectedFromFields() {
      if (!selected) return;
      const left = Number(leftField.value);
      const top = Number(topField.value);
      if (Number.isFinite(left) && Number.isFinite(top)) {
        setItemPosition(selected, left, top);
        render();
      }
    }

    function markerCenterPx(item) {
      const width = stage.clientWidth;
      const height = stage.clientHeight;
      return {
        x: parsePct(item.left) * width / 100,
        y: parsePct(item.top) * height / 100
      };
    }

    function channelPortPx(device, channel) {
      const marker = stage.querySelector(`[data-id="${CSS.escape(markerId(device))}"]`);
      const center = markerCenterPx(device);
      if (!marker || !device.channels || device.channels.length <= 1) return center;

      const channelNode = marker.querySelector(`[data-channel="${channel}"]`);
      if (!channelNode) return center;

      const stageRect = stage.getBoundingClientRect();
      const rect = channelNode.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2 - stageRect.left,
        y: rect.top + rect.height / 2 - stageRect.top
      };
    }

    function renderWires(items) {
      wires.innerHTML = '';
      const width = stage.clientWidth;
      const height = stage.clientHeight;
      wires.setAttribute('viewBox', `0 0 ${width} ${height}`);
      if (mode() === 'load') return;
      const devices = new Map(items.filter(item => item.kind === 'device').map(item => [item.key, item]));
      const loads = (data[floor()] || []).map((control, index) => ({
        key: normalizeDevice(control.entity || ''),
        channel: channelNumber(control.entity),
        left: control.left,
        top: control.top,
        index
      }));
      loads.forEach(load => {
        const device = devices.get(load.key);
        if (!device) return;
        const port = channelPortPx(device, load.channel);
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.classList.add('channel-wire');
        line.setAttribute('x1', port.x);
        line.setAttribute('y1', port.y);
        line.setAttribute('x2', parsePct(load.left) * width / 100);
        line.setAttribute('y2', parsePct(load.top) * height / 100);
        wires.appendChild(line);
      });
    }

    function render() {
      const currentFloor = floor();
      map.src = `assets/${images[currentFloor]}`;
      map.alt = `Mapa ${currentFloor}`;

      stage.querySelectorAll('.marker').forEach(node => node.remove());
      const items = itemsForFloor();

      items.forEach(item => {
        const marker = document.createElement('button');
        marker.type = 'button';
        const isModule = item.kind === 'device' && item.channels && item.channels.length > 1;
        const isBreakerImage = item.kind === 'device' && item.entity === 'switch.tz3000_cayepv1a_ts011f';
        const isGirierImage = item.kind === 'device'
          && item.entity.startsWith('switch.')
          && !isBreakerImage
          && item.channels
          && item.channels.length >= 1
          && item.channels.length <= 4;
        const isPhotoDevice = isGirierImage || isBreakerImage;
        const isExpanded = selected && markerId(selected) === markerId(item);
        const channelText = item.channels && item.channels.length === 1 ? '1 canal' : `${item.channels?.length || 1} canais`;
        marker.className = `marker ${item.kind}${(isModule || isPhotoDevice) ? ' module' : ''}${isModule && !isPhotoDevice ? ` generic-module channels-${item.channels.length}` : ''}${isGirierImage ? ` device-image channels-${item.channels.length}` : ''}${isBreakerImage ? ' device-image breaker-image' : ''}${isExpanded ? ' expanded' : ''}`;
        marker.style.setProperty('--x', item.left);
        marker.style.setProperty('--y', item.top);
        marker.title = isGirierImage ? `${item.model} ${channelText} · ${item.title}` : (isPhotoDevice || isModule ? `${item.model} · ${item.title}` : item.title);
        marker.dataset.id = markerId(item);
        if (isBreakerImage) {
          marker.innerHTML = '<img class="device-photo" src="assets/tongou-breaker-to-q-sy2-jzt.webp?v=1" alt="">';
        } else if (isGirierImage) {
          const channelItems = item.channels.map(channel => {
            const names = (item.channelNames?.[channel] || [`Canal ${channel}`]).join(' / ');
            return { channel, names };
          });
          marker.innerHTML = `<img class="device-photo" src="assets/girier-${item.channels.length}ch-module.png?v=4" alt="">` +
            channelItems
              .map(({ channel, names }) => {
                return `<span class="channel" data-channel="${channel}" title="Canal ${channel}: ${names}"><span class="channel-label">Canal ${channel}: ${names}</span></span>`;
              })
              .join('') +
            `<span class="channel-list">${channelItems.map(({ channel, names }) => `<span>${channel}: ${names}</span>`).join('')}</span>`;
        } else if (isModule) {
          marker.innerHTML = `<span class="module-count">${item.channels.length}</span>` +
            item.channels
              .map(channel => `<span class="channel" data-channel="${channel}" title="Canal ${channel}"></span>`)
              .join('');
        } else {
          marker.innerHTML = item.kind === 'device' ? '<span class="device-symbol"></span>' : '<span>L</span>';
        }
        if (selected && markerId(selected) === markerId(item)) marker.classList.add('selected');
        marker.addEventListener('pointerdown', event => {
          event.preventDefault();
          marker.setPointerCapture(event.pointerId);
          const wasSelected = selected && markerId(selected) === markerId(item);
          dragging = {
            item,
            pointerId: event.pointerId,
            wasSelected,
            moved: false,
            startX: event.clientX,
            startY: event.clientY
          };
          if (!wasSelected) selectItem(item);
        });
        stage.appendChild(marker);
      });
      renderWires(items);

      list.innerHTML = '';
      items.forEach(item => {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'row';
        if (selected && markerId(selected) === markerId(item)) row.classList.add('selected');
        const subtitle = item.kind === 'device'
          ? `${item.model || 'Modulo Zigbee'}${item.channels ? ` · ${item.channels.length === 1 ? '1 canal' : `${item.channels.length} canais`}` : ''}`
          : `Canal ${item.channel} · ${item.entity}`;
        row.innerHTML = `<strong>${item.title}</strong><span>${subtitle}</span>`;
        row.addEventListener('click', () => {
          selected = selected && markerId(selected) === markerId(item) ? null : item;
          if (selected) {
            selectItem(item);
          } else {
            selectedTitle.textContent = 'Selecione um ponto';
            selectedMeta.textContent = 'Arraste no mapa ou ajuste as porcentagens.';
            leftField.disabled = true;
            topField.disabled = true;
            leftField.value = '';
            topField.value = '';
            render();
          }
        });
        list.appendChild(row);
      });
    }

    window.addEventListener('pointermove', event => {
      if (!dragging || event.pointerId !== dragging.pointerId) return;
      const dx = event.clientX - dragging.startX;
      const dy = event.clientY - dragging.startY;
      if (!dragging.moved && Math.hypot(dx, dy) < 3) return;
      dragging.moved = true;
      const point = stagePoint(event);
      setItemPosition(dragging.item, point.left, point.top);
      selected = dragging.item;
      leftField.value = parsePct(selected.left).toFixed(2);
      topField.value = parsePct(selected.top).toFixed(2);
      render();
    });

    window.addEventListener('pointerup', event => {
      if (!dragging || event.pointerId !== dragging.pointerId) return;
      if (dragging.wasSelected && !dragging.moved) {
        selected = null;
        selectedTitle.textContent = 'Selecione um ponto';
        selectedMeta.textContent = 'Arraste no mapa ou ajuste as porcentagens.';
        leftField.disabled = true;
        topField.disabled = true;
        leftField.value = '';
        topField.value = '';
        render();
      }
      dragging = null;
    });

    leftField.addEventListener('change', updateSelectedFromFields);
    topField.addEventListener('change', updateSelectedFromFields);
    floorSelect.addEventListener('change', () => { selected = null; render(); });
    modeSelect.addEventListener('change', () => { selected = null; render(); });
    map.addEventListener('load', () => renderWires(itemsForFloor()));
    window.addEventListener('resize', render);

    document.getElementById('save').addEventListener('click', async () => {
      status.textContent = 'Salvando...';
      const response = await fetch('api/controls', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      });
      if (!response.ok) {
        status.textContent = 'Erro ao salvar';
        return;
      }
      data = await response.json();
      status.textContent = 'Salvo';
      render();
    });

    async function boot() {
      const response = await fetch('api/controls');
      const payload = await response.json();
      data = payload.controls;
      images = payload.images;
      Object.keys(images).forEach(name => {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name === 'terreo' ? 'Terreo' : 'Superior';
        floorSelect.appendChild(option);
      });
      status.textContent = 'Pronto';
      render();
    }

    boot();
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def send_asset_headers(self, status, file_path, content_length):
        self.send_response(status)
        self.send_header(
            "Content-Type",
            mimetypes.guess_type(file_path.name)[0] or "application/octet-stream",
        )
        self.send_header("Content-Length", str(content_length))
        self.end_headers()

    def send_text(self, status, content, content_type="text/plain; charset=utf-8"):
        encoded = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, status, payload):
        self.send_text(
            status,
            json.dumps(payload, ensure_ascii=False),
            "application/json; charset=utf-8",
        )

    def do_GET(self):
        path = unquote(self.path.split("?", 1)[0])
        if path == "/":
            self.send_text(200, HTML, "text/html; charset=utf-8")
            return
        if path == "/api/controls":
            controls = load_controls()
            save_controls(controls)
            self.send_json(200, {"controls": controls, "images": floor_images()})
            return
        if path.startswith("/assets/"):
            name = Path(path.removeprefix("/assets/")).name
            file_path = ASSETS / name
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404)
                return
            data = file_path.read_bytes()
            self.send_asset_headers(200, file_path, len(data))
            self.wfile.write(data)
            return
        self.send_error(404)

    def do_HEAD(self):
        path = unquote(self.path.split("?", 1)[0])
        if path == "/":
            encoded = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            return
        if path.startswith("/assets/"):
            name = Path(path.removeprefix("/assets/")).name
            file_path = ASSETS / name
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404)
                return
            self.send_asset_headers(200, file_path, file_path.stat().st_size)
            return
        self.send_error(404)

    def do_POST(self):
        if self.path != "/api/controls":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length))
            save_controls(payload)
            rebuild_dashboard()
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})
            return
        self.send_json(200, load_controls())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Editor de posicoes: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
