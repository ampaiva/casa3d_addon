import argparse
import json
import mimetypes
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

try:
    from . import tuya_names
except ImportError:
    import tuya_names


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("CASA3D_DATA_DIR", str(ROOT)))
CONTROLS = DATA_DIR / "casa3d-controls.json"
ASSETS = ROOT / "assets"
HA_CONFIG = Path(os.getenv("CASA3D_HA_CONFIG", "/config"))
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
    if not CONTROLS.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONTROLS.write_bytes((ROOT / "casa3d-controls.json").read_bytes())
    return ensure_infrastructure(ensure_device_positions(json.loads(CONTROLS.read_text())))


def ensure_infrastructure(data):
    infrastructure = data.setdefault("infrastructure", {})
    panels = infrastructure.setdefault("panels", {})
    infrastructure.setdefault("feeds", {})
    positions = data.get("device_positions", {}).get("terreo", {})
    depot = next((point for key, point in positions.items() if "deposito" in key), {"left": "90%", "top": "65%"})
    for key, start, end, offset in (("quadro-01-21", 1, 21, -2), ("quadro-22-36", 22, 36, 2)):
        panels.setdefault(key, {"title": "Quadro %02d-%02d" % (start, end), "floor": "terreo",
            "left": pct_text(pct(depot["left"]) + offset), "top": pct_text(pct(depot["top"]) + 3),
            "image": key + ".png", "location": "Deposito (posicao aproximada)",
            "circuits": [{"number": number, "name": "", "phase": "", "entity": ""} for number in range(start, end + 1)]})
    return data


def dashboard_config():
    source = HA_CONFIG / ".storage" / "lovelace.lovelace_casa_3d"
    if source.exists():
        return json.loads(source.read_text())["data"]["config"]
    return {"views": []}


def read_registry(name, field):
    source = HA_CONFIG / ".storage" / name
    return json.loads(source.read_text())["data"][field] if source.exists() else []


def discover_sensors(controls):
    devices = {item["id"]: item for item in read_registry("core.device_registry", "devices")}
    positions = controls.setdefault("device_positions", {})
    represented = set()
    classes = {"door", "window", "opening", "motion", "occupancy", "moisture", "smoke", "gas"}
    for floor_points in positions.values():
        for key, point in floor_points.items():
            if point.get("sensor_device_id"):
                represented.add(point["sensor_device_id"])
    for entry in read_registry("core.entity_registry", "entities"):
        entity = entry["entity_id"]
        device_id = entry.get("device_id")
        device = devices.get(device_id, {})
        device_class = entry.get("device_class") or entry.get("original_device_class")
        if (not entity.startswith("binary_sensor.") or not device_id or entry.get("disabled_by")
                or device.get("disabled_by") or device_class not in classes
                or entry.get("platform") not in ("zha", "zigbee2mqtt", "deconz", "tuya", "localtuya")):
            continue
        existing = next((points[entity] for points in positions.values() if entity in points), None)
        if existing is not None:
            existing.update({"sensor_device_id": device_id, "device_class": device_class})
            represented.add(device_id)
            continue
        if device_id in represented:
            continue
        # Unlocated sensors stay in a provisional strip, never at an invented room location.
        points = positions.setdefault("terreo", {})
        index = sum(bool(point.get("sensor_device_id")) for point in points.values())
        points[entity] = {"title": entry.get("name") or device.get("name_by_user") or device.get("name") or entity,
            "left": pct_text(3 + (index // 12) * 4), "top": pct_text(8 + (index % 12) * 7),
            "sensor_device_id": device_id, "device_class": device_class, "position_pending": True}
        represented.add(device_id)
    return controls


def zigbee_devices(controls, dashboard):
    entities = {item["entity_id"]: item for item in read_registry("core.entity_registry", "entities")}
    devices = {item["id"]: item for item in read_registry("core.device_registry", "devices")}
    signals = defaultdict(dict)
    for entity, entry in entities.items():
        for kind in ("lqi", "rssi"):
            if entity.endswith("_" + kind) and entry.get("device_id") and not entry.get("disabled_by"):
                signals[entry["device_id"]][kind] = entity
    signal_map = json.loads((ASSETS / "tuya-signal-map.json").read_text())
    result = {}
    for view in dashboard.get("views", []):
        floor = view.get("path")
        if floor not in controls.get("device_positions", {}):
            continue
        positions = controls["device_positions"][floor]
        nodes = {}
        physical_ids = {}
        sensor_controls = [{"entity": key} for key, point in positions.items() if point.get("sensor_device_id")]
        for control in controls.get(floor, []) + sensor_controls:
            entity = control.get("entity", "")
            key = normalize_device(entity)
            if key not in positions:
                continue
            entry = entities.get(entity, {})
            device_id = entry.get("device_id")
            device = devices.get(device_id, {})
            platform = entry.get("platform", "")
            identified = platform in ("zha", "zigbee2mqtt", "deconz") or bool(re.search(r"zigbee|PSW-\dCH-ZT", device.get("model") or "", re.I))
            if device_id:
                physical_ids[device_id] = key
            node = nodes.setdefault(key, {"key": key, "is_zigbee": identified, "platform": platform,
                "device_id": device_id, "model": device.get("model"), "signals": dict(signals[device_id]),
                "entity": entity, "network": "Sonoff/ZHA" if platform == "zha" else platform})
            if entity in signal_map:
                node["signals"]["lqi"] = signal_map[entity]["sensor"]
                node["is_zigbee"] = True

        source = next((card for card in view.get("cards", []) if card.get("type") == "picture-elements"), {})
        legacy = [child for element in source.get("elements", [])
            if any(condition.get("entity") == "input_boolean.casa3d_mostrar_zigbee" for condition in element.get("conditions", []))
            for child in element.get("elements", [])]
        anchors = []
        for element in legacy:
            if element.get("type") not in ("icon", "state-icon"):
                continue
            entity = element.get("entity")
            entry = entities.get(entity, {})
            device_id = entry.get("device_id")
            title = element.get("title", "")
            network = "Sonoff/ZHA" if "ZHA" in title else "X5/Tuya"
            key = physical_ids.get(device_id) or (normalize_device(entity) if entity else "zigbee:" + network)
            style = element.get("style", {})
            if "left" not in style or "top" not in style:
                continue
            positions.setdefault(key, {"title": title.split(" · ")[-1], "left": style["left"], "top": style["top"],
                "model": devices.get(device_id, {}).get("model") or network})
            node = nodes.setdefault(key, {"key": key, "signals": dict(signals[device_id]), "entity": entity,
                "device_id": device_id, "model": positions[key].get("model")})
            node.update({"is_zigbee": True, "network": network, "icon": element.get("icon", "mdi:access-point-network")})
            anchors.append((key, pct(style["left"]), pct(style["top"])))

        links = []
        for image in (element for element in legacy if element.get("type") == "image"):
            name = Path(image.get("image", "")).name
            mesh = HA_CONFIG / "www" / "casa3d" / name
            if not mesh.is_file():
                mesh = ASSETS / name
            if not mesh.is_file() or mesh.suffix != ".svg":
                continue
            svg = ET.parse(mesh).getroot()
            _, _, width, height = map(float, svg.attrib["viewBox"].split())
            def anchor(x, y):
                candidates = [(abs(px * width / 100 - x) + abs(py * height / 100 - y), key) for key, px, py in anchors]
                distance, key = min(candidates, default=(float("inf"), None))
                return key if distance < 3 else None
            for path in svg.findall(".//{http://www.w3.org/2000/svg}path"):
                match = re.fullmatch(r"M\s*([\d.]+)[ ,]+([\d.]+)\s*L\s*([\d.]+)[ ,]+([\d.]+)", path.get("d", ""))
                if not match:
                    continue
                x1, y1, x2, y2 = map(float, match.groups())
                start, end = anchor(x1, y1), anchor(x2, y2)
                if not start or not end:
                    continue
                links.append({"from": start, "to": end, "title": path.findtext("{http://www.w3.org/2000/svg}title", ""),
                    "color": path.get("stroke", "#0c8cab"), "dashed": bool(path.get("stroke-dasharray")), "opacity": path.get("opacity", "0.5")})
        result[floor] = {"nodes": nodes, "links": links}
    return result


def floor_images():
    images = dict(FLOOR_IMAGES)
    if OPTIONS_PATH.exists():
        try:
            options = json.loads(OPTIONS_PATH.read_text())
        except json.JSONDecodeError:
            options = {}
        images["terreo"] = options.get("floor_ground_image") or images["terreo"]
        images["superior"] = options.get("floor_upper_image") or images["superior"]
    for view in dashboard_config().get("views", []):
        for card in view.get("cards", []):
            if view.get("path") in images and card.get("type") == "picture-elements":
                images[view["path"]] = card["image"].rsplit("/", 1)[-1]
    return images


def save_controls(data):
    ensure_device_positions(data)
    ensure_infrastructure(data)
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
  <title>Casa 3D</title>
  <script src="assets/lucide.min.js"></script>
  <script src="assets/chart.umd.js"></script>
  <script src="assets/marked.umd.js"></script>
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
    header { height: 44px; padding: 0 10px; gap: 8px; }
    h1 { font-size: 15px; white-space: nowrap; }
    main { height: calc(100dvh - 44px); grid-template-columns: minmax(0, 1fr); }
    aside, .edit-only { display: none; }
    body.editing main { grid-template-columns: minmax(0, 1fr) 250px; }
    body.editing aside { display: block; padding: 10px; }
    body.editing .edit-only { display: inline-flex; }
    body.editing .details p, .legend { display: none; }
    .stageWrap { padding: 0; position: relative; }
    .stage { width: 100%; max-width: 1400px; border: 0; touch-action: pan-y; }
    body.editing .stage { touch-action: none; }
    .status { min-width: 0; }
    .toolbar { flex-wrap: nowrap; gap: 4px; }
    .icon-button { width: 34px; height: 34px; padding: 6px; min-height: 34px; display: grid; place-items: center; }
    .icon-button svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 2; }
    .icon-button[aria-pressed="true"] { color: #047b9d; background: #e0f3f7; border-color: #72bacb; }
    .layer-tools { display: flex; gap: 4px; border-left: 1px solid var(--line); padding-left: 6px; }
    .marker { cursor: pointer; }
    body.editing .marker { cursor: grab; }
    .marker.module.device-image { min-width: 0; }
    .channel-list { grid-template-columns: 1fr; max-width: 240px; white-space: normal; width: max-content; }
    .channel-list span { padding: 3px; cursor: pointer; pointer-events: auto; }
    .ha-layer { position: absolute; inset: 0; pointer-events: none; }
    .ha-layer > * { pointer-events: auto; display: block; --ha-card-border-width: 0; --ha-card-border-radius: 0; --ha-card-box-shadow: none; }
    #electrical { display: none; padding: 12px; gap: 16px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    body.electrical-page #electrical { display: grid; }
    body.electrical-page .stage { display: none; }
    .ha-section { display: grid; align-content: start; gap: 12px; min-width: 0; }
    .live-card { border-bottom: 1px solid var(--line); padding: 8px 0 16px; min-width: 0; }
    .live-card h2 { font-size: 16px; margin: 0 0 12px; }
    .live-card h3 { font-size: 13px; margin: 16px 0 4px; }
    .entity-row { display: flex; width: 100%; justify-content: space-between; gap: 12px; border: 0; border-radius: 0; padding: 8px 0; background: transparent; text-align: left; }
    .entity-row span { overflow-wrap: anywhere; }
    .entity-row strong { white-space: nowrap; }
    .chart-box { height: 220px; }
    .table-scroll { overflow: auto; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { padding: 8px; text-align: left; border-bottom: 1px solid var(--line); }
    .live-element { position: absolute; min-height: 0; border: 0; padding: 0; }
    .live-element svg, .marker svg { width: 22px; height: 22px; fill: currentColor; stroke: none; display: block; }
    .live-element img { width: 100%; height: auto; }
    .marker.on { background: #efc532; color: #242424; }
    .floor-fixture {
      position: absolute;
      left: var(--x);
      top: var(--y);
      transform: translate(-50%, -50%);
      width: clamp(8px, 1.15%, 18px);
      aspect-ratio: 1;
      min-height: 0;
      padding: 0;
      border: 0;
      border-radius: 50%;
      background: transparent;
      z-index: 1;
      overflow: visible;
    }
    .floor-fixture svg { width: 100%; height: 100%; display: block; overflow: visible; }
    .floor-fixture .spot-lens { fill: #555b55; transition: fill 180ms ease; }
    .floor-fixture::before {
      content: '';
      position: absolute;
      inset: -180%;
      pointer-events: none;
      border-radius: 50%;
      background: radial-gradient(circle, #ffdd9b88 0%, #ffc66a38 30%, #ffc66a00 70%);
      opacity: 0;
      transition: opacity 180ms ease;
    }
    .floor-fixture.on::before { opacity: 1; }
    .floor-fixture.on .spot-lens { fill: #fff1c5; filter: drop-shadow(0 0 2px #ffc86c); }
    .floor-fixture:focus-visible { outline: 2px solid #0f8db3; outline-offset: 4px; }
    .network-badge { position: absolute; right: -5px; top: -5px; width: 12px; height: 12px; border-radius: 50%; background: var(--network-color); border: 1px solid white; font-size: 8px !important; display: grid; place-items: center; pointer-events: none; }
    .network-info { position: absolute; top: calc(100% + 5px); left: 50%; transform: translateX(-50%); padding: 2px 4px; background: rgba(16,24,28,.9); border-radius: 3px; font-size: 9px !important; line-height: 13px !important; white-space: nowrap; pointer-events: none; }
    .expanded .network-info { top: auto; bottom: calc(100% + 5px); }
    .mesh { z-index: 1; }
    .mesh line { stroke-width: 1.5; }
    #connection { color: #a34b16; font-size: 12px; }
    .marker.panel { width: 32px; height: 40px; min-width: 0; padding: 2px; border-radius: 4px; background: white; }
    .marker.panel img { width: 100%; height: 100%; object-fit: contain; }
    .marker.signal-good { outline: 3px solid #278152; }
    .marker.signal-fair { outline: 3px solid #d4a21e; }
    .marker.signal-poor { outline: 3px solid #c64450; }
    .marker.signal-unknown { outline: 2px dashed #858c90; }
    #signalLegend { display: none; position: sticky; bottom: 0; padding: 6px 10px; background: #fffffff0; gap: 14px; flex-wrap: wrap; font-size: 11px; z-index: 10; }
    #signalLegend span { display: inline-flex; gap: 5px; align-items: center; }
    #signalLegend b { width: 8px; height: 8px; border-radius: 50%; }
    #feedFields { display: none; gap: 6px; margin-top: 10px; font-size: 12px; }
    #feedFields label { display: grid; gap: 4px; }
    #feedFields select { width: 100%; min-width: 0; padding: 5px; font-size: 12px; }
    dialog { border: 1px solid var(--line); border-radius: 6px; padding: 0; width: min(1000px, 96vw); max-height: 92dvh; color: var(--text); }
    dialog::backdrop { background: #1118; }
    .panel-heading { display: flex; align-items: center; gap: 8px; padding: 10px 16px; border-bottom: 1px solid var(--line); }
    .panel-heading h2 { margin: 0; font-size: 17px; flex: 1; }
    .panel-content { display: grid; grid-template-columns: minmax(200px, 36%) minmax(0, 1fr); gap: 16px; padding: 16px; }
    #panelPhoto { width: 100%; max-height: 68dvh; object-fit: contain; }
    #panelDialog table { min-width: 450px; }
    #panelDialog input, #panelDialog select { width: 100%; font-size: 12px; padding: 5px; min-height: 30px; }
    #panelDialog th, #panelDialog td { padding: 6px 4px; }
    #panelDialog td:first-child { width: 32px; }
    .circuit-devices { display: block; font-size: 11px; color: var(--muted); }
    .panel-actions { display: flex; justify-content: flex-end; gap: 8px; padding: 10px 16px; border-top: 1px solid var(--line); }
    .panel-meta { padding: 0 16px; font-size: 12px; color: var(--muted); }
    .panel-heading { position: sticky; top: 0; background: white; z-index: 2; }
    .panel-actions { position: sticky; bottom: 0; background: white; }
    .panels-overview { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 16px; }
    .panels-overview .entity-row { width: auto; font-size: 13px; }
    header { height: auto; min-height: 44px; flex-wrap: wrap; }
    .toolbar { flex-wrap: wrap; }
    main { height: calc(100dvh - var(--header-height, 44px)); }
    @media (max-width: 700px) {
      .icon-button { width: 30px; height: 30px; min-height: 30px; padding: 5px; }
      .layer-tools { gap: 2px; padding-left: 3px; }
      .panel-content { grid-template-columns: 1fr; }
      #panelPhoto { max-height: 35dvh; }
      .panel-heading { padding: 8px; }
      .panel-actions { flex-wrap: wrap; }
    }
    @media (max-width: 700px) {
      header h1, #status { display: none; }
      header { justify-content: center; }
      button, select { font-size: 12px; padding: 0 6px; }
      .toolbar { width: 100%; justify-content: space-between; }
      body.editing main { grid-template-columns: 1fr; }
      body.editing aside { position: fixed; bottom: 0; left: 0; right: 0; max-height: 30dvh; z-index: 25; }
      body.editing .stageWrap { padding-bottom: 30dvh; }
      #electrical { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Casa 3D</h1>
    <div class="toolbar">
      <select id="floor" aria-label="Visao"></select>
      <div class="layer-tools" aria-label="Camadas">
        <button class="icon-button" id="devicesLayer" title="Dispositivos" aria-label="Dispositivos" aria-pressed="true"><i data-lucide="circuit-board"></i></button>
        <button class="icon-button" id="loadsLayer" title="Lampadas e cargas" aria-label="Lampadas e cargas" aria-pressed="false"><i data-lucide="lightbulb"></i></button>
        <button class="icon-button" id="panelsLayer" title="Quadros" aria-label="Quadros" aria-pressed="false"><i data-lucide="panels-top-left"></i></button>
        <button class="icon-button" id="zigbeeLayer" title="Rede Zigbee" aria-label="Rede Zigbee" aria-pressed="false"><i data-lucide="network"></i></button>
        <button class="icon-button" id="signalLayer" title="Sinal Zigbee" aria-label="Sinal Zigbee" aria-pressed="false"><i data-lucide="radio"></i></button>
        <button class="icon-button" id="electricLayer" title="Mapa eletrico" aria-label="Mapa eletrico" aria-pressed="false"><i data-lucide="utility-pole"></i></button>
      </div>
      <button id="edit" class="icon-button" title="Editar posicoes" aria-label="Editar posicoes" aria-pressed="false"><i data-lucide="square-pen"></i></button>
      <button id="refreshNames" class="icon-button" title="Atualizar nomes Tuya" aria-label="Atualizar nomes Tuya"><i data-lucide="refresh-cw"></i></button>
      <button id="save" class="primary edit-only">Salvar</button>
      <span id="status" class="status edit-only"></span>
    </div>
  </header>
  <main>
    <section class="stageWrap">
      <div id="stage" class="stage">
        <img id="map" alt="">
        <div id="haLayer" class="ha-layer"></div>
        <svg id="mesh" class="wire mesh"></svg>
        <svg id="wires" class="wire"></svg>
      </div>
      <div id="electrical"></div>
      <div id="signalLegend"><span><b style="background:#278152"></b>Bom</span><span><b style="background:#d4a21e"></b>Intermediario</span><span><b style="background:#c64450"></b>Fraco</span><span><b style="background:#858c90"></b>Sem leitura</span></div>
      <div id="connection" role="status"></div>
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
        <div id="feedFields"><label>Quadro<select id="feedPanel"></select></label><label>Circuito<select id="feedCircuit"></select></label><label>Fase<select id="feedPhase"></select></label></div>
      </div>
      <div id="list" class="list"></div>
    </aside>
  </main>
  <dialog id="panelDialog" aria-labelledby="panelTitle">
    <div class="panel-heading"><h2 id="panelTitle"></h2><button id="panelEdit" class="icon-button" title="Editar circuitos" aria-label="Editar circuitos"><i data-lucide="square-pen"></i></button><button id="panelClose" class="icon-button" title="Fechar quadro" aria-label="Fechar quadro"><i data-lucide="x"></i></button></div>
    <p id="panelLocation" class="panel-meta"></p>
    <div class="panel-content"><img id="panelPhoto" alt=""><div class="table-scroll"><table><thead><tr><th>N.</th><th>Circuito / dispositivos</th><th>Fase</th><th>Leitura HA</th></tr></thead><tbody id="panelCircuits"></tbody></table></div></div>
    <div class="panel-actions"><span id="panelStatus" role="status"></span><button id="panelCancel">Cancelar</button><button id="panelSave" class="primary">Salvar</button></div>
  </dialog>
  <script>
    const stage = document.getElementById('stage');
    const map = document.getElementById('map');
    const wires = document.getElementById('wires');
    const floorSelect = document.getElementById('floor');
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
    let editing = false;
    let dashboard = {views: []};
    let icons = {};
    let fixtures = {};
    let zigbee = {};
    let haCards = [];
    let charts = [];
    let haLayerKey = '';
    const layers = {devices: true, loads: false, panels: false, zigbee: false, signal: false, electric: false};
    const escapeHtml = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

    function hass() {
      try { return window.parent.document.querySelector('home-assistant')?.hass; }
      catch { return null; }
    }

    function moreInfo(entity) {
      const host = window.parent.document.querySelector('home-assistant');
      host?.dispatchEvent(new window.parent.CustomEvent('hass-more-info', {detail: {entityId: entity}, bubbles: true, composed: true}));
    }

    async function toggleEntity(entity) {
      const ha = hass();
      if (!ha) return;
      if (!['switch', 'light', 'input_boolean'].includes(entity.split('.')[0])) return moreInfo(entity);
      try { await ha.callService('homeassistant', 'toggle', {entity_id: entity}); }
      catch { document.getElementById('connection').textContent = 'Nao foi possivel acionar o dispositivo.'; }
    }

    function iconHtml(name) {
      return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[name] || icons['mdi:lightbulb'] || ''}"></path></svg>`;
    }

    function updateFixtures() {
      stage.querySelectorAll('.floor-fixture').forEach(node => {
        const state = hass()?.states[node.dataset.entity]?.state;
        node.classList.toggle('on', state === 'on');
        node.setAttribute('aria-pressed', String(state === 'on'));
        node.disabled = !hass() || !['on', 'off'].includes(state);
      });
    }

    function renderFixtures() {
      stage.querySelectorAll('.floor-fixture').forEach(node => node.remove());
      if (!layers.loads || floor() === 'eletrica') return;
      for (const fixture of fixtures[floor()] || []) {
        if (fixture.type !== 'recessed-ground-spot') continue;
        for (const [index, point] of fixture.points.entries()) {
          const node = document.createElement('button');
          node.type = 'button';
          node.className = 'floor-fixture';
          node.dataset.entity = fixture.entity;
          node.style.setProperty('--x', point.left);
          node.style.setProperty('--y', point.top);
          node.title = `${fixture.title} - spot ${index + 1}`;
          node.setAttribute('aria-label', node.title);
          node.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="11" fill="#262b28" stroke="#969d98" stroke-width="1.2"/><circle cx="12" cy="12" r="8.5" fill="#101612"/><circle class="spot-lens" cx="12" cy="12" r="5.8"/><path d="M7 4.6A9 9 0 0 1 17 4.6" fill="none" stroke="#d8ded8" stroke-width="1"/><circle cx="3.7" cy="12" r=".65" fill="#bac1bc"/><circle cx="20.3" cy="12" r=".65" fill="#bac1bc"/></svg>';
          node.addEventListener('click', () => { if (!editing) toggleEntity(fixture.entity); });
          stage.appendChild(node);
        }
      }
      updateFixtures();
    }

    function entityValue(entity) {
      const state = hass()?.states[entity];
      return state ? `${state.state} ${state.attributes.unit_of_measurement || ''}`.trim() : 'Indisponivel';
    }

    function mountElements(elements, target) {
      for (const config of elements) {
        if (config.type === 'conditional') {
          const group = document.createElement('div');
          target.appendChild(group);
          const update = () => { group.style.display = (config.conditions || []).every(condition => {
            const state = hass()?.states[condition.entity]?.state;
            return condition.state !== undefined ? state === condition.state : state !== condition.state_not;
          }) ? 'contents' : 'none'; };
          haCards.push(update);
          update();
          mountElements(config.elements || [], group);
          continue;
        }
        const node = document.createElement(config.tap_action?.action === 'none' || !config.entity ? 'div' : 'button');
        node.className = 'live-element';
        for (const [key, value] of Object.entries(config.style || {})) node.style.setProperty(key, value);
        node.title = config.title || config.entity || '';
        if (node.tagName === 'BUTTON') {
          node.type = 'button';
          node.setAttribute('aria-label', node.title);
          node.addEventListener('click', () => config.tap_action?.action === 'toggle' ? toggleEntity(config.entity) : moreInfo(config.entity));
        }
        if (config.type === 'image') {
          const image = document.createElement('img');
          image.src = config.image;
          image.alt = config.title || '';
          node.appendChild(image);
        } else if (config.type === 'state-label') {
          const update = () => { node.textContent = `${config.prefix || ''}${entityValue(config.entity)}${config.suffix || ''}`; };
          haCards.push(update);
          update();
        } else if (config.style?.color !== 'transparent') {
          node.innerHTML = iconHtml(config.icon);
          if (config.state_color) {
            const update = () => { node.style.color = hass()?.states[config.entity]?.state === 'on' ? '#efc532' : (config.style?.color || '#65a8cd'); };
            haCards.push(update);
            update();
          }
        }
        target.appendChild(node);
      }
    }

    async function mountCard(config, target) {
      const card = document.createElement('section');
      card.className = 'live-card';
      target.appendChild(card);
      const heading = document.createElement('h2');
      heading.textContent = config.title || config.heading || '';
      card.appendChild(heading);
      if (config.type === 'entities') {
        for (const entry of config.entities || []) {
          if (entry.type === 'section') {
            const title = document.createElement('h3'); title.textContent = entry.label; card.appendChild(title); continue;
          }
          const entity = typeof entry === 'string' ? entry : entry.entity;
          const row = document.createElement('button');
          row.className = 'entity-row';
          const title = document.createElement('span');
          title.textContent = entry.name || hass()?.states[entity]?.attributes.friendly_name || entity;
          const value = document.createElement('strong');
          const update = () => { value.textContent = entityValue(entity); };
          update(); haCards.push(update);
          row.append(title, value);
          row.addEventListener('click', () => moreInfo(entity));
          card.appendChild(row);
        }
      } else if (config.type === 'markdown') {
        const parsed = new DOMParser().parseFromString(marked.parse(config.content || ''), 'text/html');
        const table = document.createElement('table');
        for (const sourceRow of parsed.querySelectorAll('tr')) {
          const row = document.createElement('tr');
          for (const sourceCell of sourceRow.children) { const cell = document.createElement(sourceCell.tagName === 'TH' ? 'th' : 'td'); cell.textContent = sourceCell.textContent; row.appendChild(cell); }
          table.appendChild(row);
        }
        const scroll = document.createElement('div'); scroll.className = 'table-scroll'; scroll.appendChild(table); card.appendChild(scroll);
      } else if (config.type === 'history-graph') {
        const box = document.createElement('div'); box.className = 'chart-box';
        const canvas = document.createElement('canvas'); box.appendChild(canvas); card.appendChild(box);
        const ha = hass();
        if (!ha) { box.textContent = 'Historico disponivel no Home Assistant'; return; }
        const entries = config.entities.map(entry => typeof entry === 'string' ? {entity: entry, name: entry} : entry);
        try {
          const start = new Date(Date.now() - (config.hours_to_show || 3) * 3600000).toISOString();
          const history = await ha.callApi('GET', `history/period/${start}?filter_entity_id=${encodeURIComponent(entries.map(e => e.entity).join(','))}`);
          if (!canvas.isConnected) return;
          const colors = ['#0c8cab', '#c16232', '#479651'];
          const chart = new Chart(canvas, {type:'line', data:{datasets:entries.map((entry, index) => ({label:entry.name, borderColor:colors[index % colors.length], borderWidth:1.5, pointRadius:0, data:(history.find(series => series[0]?.entity_id === entry.entity) || []).filter(s => Number.isFinite(Number(s.state))).map(s => ({x:Date.parse(s.last_changed), y:Number(s.state)}))}))}, options:{responsive:true, maintainAspectRatio:false, animation:false, scales:{x:{type:'linear', ticks:{callback:value => new Date(value).toLocaleTimeString('pt-BR', {hour:'2-digit', minute:'2-digit'}), maxTicksLimit:5}}, y:{beginAtZero:true}}}});
          charts.push(chart);
        } catch { box.textContent = 'Nao foi possivel carregar o historico.'; }
      }
    }

    function renderHaLayers() {
      const key = JSON.stringify([floor(), layers.zigbee, layers.electric]);
      if (key === haLayerKey) return;
      haLayerKey = key;
      const target = document.getElementById('haLayer');
      const electrical = document.getElementById('electrical');
      target.replaceChildren();
      electrical.replaceChildren();
      charts.forEach(chart => chart.destroy());
      charts = [];
      haCards = [];
      document.body.classList.toggle('electrical-page', floor() === 'eletrica');
      const view = dashboard.views.find(v => v.path === floor());
      if (!view) return;
      if (floor() === 'eletrica') {
        const overview = document.createElement('div'); overview.className = 'panels-overview';
        for (const [id, panel] of Object.entries(data.infrastructure.panels)) {
          const button = document.createElement('button'); button.className = 'entity-row';
          button.textContent = panel.title + ' · Deposito'; button.addEventListener('click', () => openPanel(id)); overview.appendChild(button);
        }
        electrical.appendChild(overview);
        for (const section of view.sections || []) {
          const column = document.createElement('div');
          column.className = 'ha-section';
          electrical.appendChild(column);
          for (const config of section.cards || []) mountCard(config, column);
        }
        return;
      }
      const source = view.cards?.find(c => c.type === 'picture-elements');
      if (!source) return;
      const elements = (source.elements || []).filter(element => {
        if (element.entity?.startsWith('input_boolean.casa3d_mostrar_')) return false;
        const condition = element.conditions?.find(c => c.entity?.startsWith('input_boolean.casa3d_mostrar_'));
        if (!condition) return layers.electric;
        if (condition.entity.endsWith('dispositivos')) return false;
        return condition.entity.endsWith('zigbee') ? false : layers.electric;
      }).flatMap(element => element.conditions?.some(c => c.entity?.startsWith('input_boolean.casa3d_mostrar_')) ? element.elements : [element]);
      mountElements(elements, target);
    }

    const normalizeDevice = (entity) => entity
      .replace(/_switch_[1-4](_2)?$/, '')
      .replace(/_socket_1$/, '');
    const channelNumber = (entity) => {
      const match = String(entity || '').match(/_switch_([1-4])(_2)?$/);
      return match ? Number(match[1]) : 1;
    };
    const parsePct = (value) => Number(String(value).replace('%', ''));
    const fmtPct = (value) => `${Math.max(0, Math.min(100, value)).toFixed(2)}%`;
    let channelNames = {};
    const controlTitle = control => channelNames[control.entity] || control.title || control.entity;
    const phases = ['', 'L1', 'L2', 'L3', 'L1+L2', 'L1+L3', 'L2+L3', 'L1+L2+L3'];
    const phaseColors = {'L1':'#b94753', 'L2':'#278152', 'L3':'#8959ac'};
    let panelId = null, panelDraft = null, panelEditing = false;

    function options(select, entries, value) {
      select.replaceChildren();
      entries.forEach(([key, label]) => { const option = document.createElement('option'); option.value = key; option.textContent = label; select.appendChild(option); });
      select.value = value || '';
    }

    function renderFeedFields() {
      const fields = document.getElementById('feedFields');
      fields.style.display = editing && selected?.kind === 'device' && !selected.key.startsWith('zigbee:') ? 'grid' : 'none';
      if (fields.style.display === 'none') return;
      const feed = data.infrastructure.feeds[selected.key] || {};
      options(document.getElementById('feedPanel'), [['','Nao cadastrado'], ...Object.entries(data.infrastructure.panels).map(([id,p]) => [id,p.title])], feed.panel);
      const panel = data.infrastructure.panels[feed.panel];
      options(document.getElementById('feedCircuit'), [['','Nao cadastrado'], ...(panel?.circuits || []).map(c => [String(c.number), `${c.number} · ${c.name || 'Sem nome'}`])], String(feed.circuit || ''));
      options(document.getElementById('feedPhase'), phases.map(p => [p, p || 'Fase do circuito / nao cadastrada']), feed.phase);
    }
    for (const id of ['feedPanel','feedCircuit','feedPhase']) {
      document.getElementById(id).addEventListener('change', () => {
        if (!selected || selected.kind !== 'device') return;
        data.infrastructure.feeds[selected.key] = {
          panel: document.getElementById('feedPanel').value,
          circuit: id === 'feedPanel' ? '' : document.getElementById('feedCircuit').value,
          phase: document.getElementById('feedPhase').value
        };
        status.textContent = 'Alteracoes nao salvas'; renderFeedFields(); renderWires(itemsForFloor());
      });
    }

    function openPanel(id) {
      panelId = id; panelDraft = structuredClone(data.infrastructure.panels[id]); panelEditing = false;
      document.getElementById('panelStatus').textContent = '';
      renderPanel(); document.getElementById('panelDialog').showModal();
    }
    function renderPanel() {
      document.getElementById('panelTitle').textContent = panelDraft.title;
      document.getElementById('panelLocation').textContent = panelDraft.location;
      document.getElementById('panelPhoto').src = 'assets/' + panelDraft.image;
      document.getElementById('panelPhoto').alt = panelDraft.title + ' · Deposito';
      const tbody = document.getElementById('panelCircuits'); tbody.replaceChildren();
      const entityChoices = [['','Sem leitura HA'], ...Object.entries(hass()?.states || {}).filter(([id]) => id.startsWith('sensor.')).map(([id,s]) => [id, s.attributes.friendly_name || id])];
      for (const circuit of panelDraft.circuits) {
        const row = document.createElement('tr');
        const number = document.createElement('td'); number.textContent = circuit.number;
        const name = document.createElement('td');
        const phase = document.createElement('td'); const reading = document.createElement('td');
        if (panelEditing) {
          const input = document.createElement('input'); input.value = circuit.name; input.setAttribute('aria-label', 'Nome do circuito ' + circuit.number);
          input.addEventListener('input', () => circuit.name = input.value); name.appendChild(input);
          const choice = document.createElement('select'); choice.setAttribute('aria-label', 'Fase do circuito ' + circuit.number);
          options(choice, phases.map(p => [p,p || 'Nao cadastrada']), circuit.phase); choice.addEventListener('change', () => circuit.phase = choice.value); phase.appendChild(choice);
          const sensor = document.createElement('select'); sensor.setAttribute('aria-label', 'Leitura do circuito ' + circuit.number);
          options(sensor, entityChoices, circuit.entity); sensor.addEventListener('change', () => circuit.entity = sensor.value); reading.appendChild(sensor);
        } else {
          name.textContent = circuit.name || 'Nao cadastrado'; phase.textContent = circuit.phase || 'Nao cadastrada';
          reading.textContent = circuit.entity ? entityValue(circuit.entity) : 'Sem leitura'; reading.dataset.entity = circuit.entity || '';
        }
        const assigned = Object.entries(data.infrastructure.feeds).filter(([,f]) => f.panel === panelId && String(f.circuit) === String(circuit.number)).map(([key]) => {
          return Object.values(data.device_positions).map(points => points[key]?.title).find(Boolean) || key;
        });
        if (assigned.length) { const labels = document.createElement('span'); labels.className = 'circuit-devices'; labels.textContent = assigned.join(', '); name.appendChild(labels); }
        row.append(number, name, phase, reading); tbody.appendChild(row);
      }
      document.getElementById('panelCancel').hidden = !panelEditing;
      document.getElementById('panelSave').hidden = !panelEditing;
      document.getElementById('panelEdit').setAttribute('aria-pressed', String(panelEditing));
    }
    document.getElementById('panelEdit').addEventListener('click', () => { panelEditing = !panelEditing; renderPanel(); });
    document.getElementById('panelClose').addEventListener('click', () => document.getElementById('panelDialog').close());
    document.getElementById('panelCancel').addEventListener('click', () => { panelDraft = structuredClone(data.infrastructure.panels[panelId]); panelEditing = false; renderPanel(); });
    function applyPanel() { data.infrastructure.panels[panelId] = structuredClone(panelDraft); status.textContent = 'Alteracoes nao salvas'; render(); }
    document.getElementById('panelSave').addEventListener('click', async () => {
      applyPanel(); const button = document.getElementById('panelSave'); button.disabled = true;
      try { await persistControls(); panelEditing = false; renderPanel(); document.getElementById('panelStatus').textContent = 'Salvo'; }
      catch { document.getElementById('panelStatus').textContent = 'Erro ao salvar'; }
      finally { button.disabled = false; }
    });

    async function refreshNames(force = false) {
      const button = document.getElementById('refreshNames');
      button.disabled = true;
      try {
        const response = await fetch('api/channel-names', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({force})
        });
        if (!response.ok) throw new Error('Falha ao atualizar nomes Tuya.');
        const result = await response.json();
        channelNames = result.names || channelNames;
        button.title = result.error || 'Nomes Tuya atualizados';
        if (force && result.error) alert(result.error);
        render();
      } catch (error) {
        button.title = 'Falha ao atualizar nomes Tuya; nomes anteriores preservados.';
        if (force) alert(button.title);
      } finally { button.disabled = false; }
    }
    document.getElementById('refreshNames').addEventListener('click', () => refreshNames(true));

    function markerId(item) {
      return `${item.kind}:${item.key}:${item.index ?? ''}`;
    }

    function floor() {
      return floorSelect.value;
    }

    function itemsForFloor() {
      const currentFloor = floor();
      const controls = data[currentFloor] || [];
      const devices = data.device_positions?.[currentFloor] || {};
      const items = [];

      if (layers.loads) {
        controls.forEach((control, index) => {
          const key = normalizeDevice(control.entity || '');
          items.push({
            kind: 'load',
            key,
            channel: channelNumber(control.entity),
            index,
            title: controlTitle(control),
            entity: control.entity,
            icon: control.icon,
            left: control.left,
            top: control.top
          });
        });
      }

      if (layers.devices || layers.zigbee || layers.signal) {
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
          const title = controlTitle(control);
          if (!acc[key][channel].includes(title)) acc[key][channel].push(title);
          return acc;
        }, {});
        Object.entries(devices).forEach(([key, point]) => {
          const network = zigbee[currentFloor]?.nodes[key];
          const hub = key.startsWith('zigbee:');
          if (hub ? !layers.zigbee && !layers.signal : !layers.devices && !((layers.zigbee || layers.signal) && network?.is_zigbee)) return;
          const channels = Array.from(channelsByDevice[key] || [1]).sort((a, b) => a - b);
          const isSwitchDevice = key.startsWith('switch.');
          const isBreakerDevice = key === 'switch.tz3000_cayepv1a_ts011f';
          items.push({
            kind: 'device',
            key,
            title: point.title || key,
            entity: network?.entity || key,
            network,
            deviceClass: point.device_class,
            positionPending: point.position_pending,
            left: point.left,
            top: point.top,
            model: point.device_class ? 'Sensor' : isBreakerDevice ? 'Disjuntor Zigbee' : (isSwitchDevice ? 'Girier' : (point.model || (channels.length > 1 ? 'Modulo multicanal' : 'Modulo Zigbee'))),
            channels,
            channelNames: channelNamesByDevice[key] || {}
          });
        });
      }

      if (layers.panels) for (const [key, panel] of Object.entries(data.infrastructure.panels)) {
        if (panel.floor === currentFloor) items.push({kind:'panel', key, title:panel.title, left:panel.left, top:panel.top, image:panel.image, entity:''});
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
      } else if (item.kind === 'panel') {
        const panel = data.infrastructure.panels[item.key]; panel.left = fmtPct(left); panel.top = fmtPct(top);
        panel.location = 'Deposito'; item.left = panel.left; item.top = panel.top;
      } else {
        const point = data.device_positions[floor()][item.key];
        point.left = fmtPct(left);
        point.top = fmtPct(top);
        delete point.position_pending;
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
      selectedMeta.textContent = item.kind === 'panel' ? 'Quadro eletrico · Deposito' : item.kind === 'device'
        ? `${item.model || 'Modulo Zigbee'} fisico${item.positionPending ? ' · Posicao a confirmar' : ''}${item.channels && item.channels.length > 1 ? ` · ${item.channels.length} canais` : ''}: ${item.entity}`
        : `Canal ${item.channel} alimenta: ${item.entity}`;
      leftField.disabled = false;
      topField.disabled = false;
      leftField.value = parsePct(item.left).toFixed(2);
      topField.value = parsePct(item.top).toFixed(2);
      render();
      renderFeedFields();
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
      if (floor() === 'eletrica') return;
      const devices = new Map(items.filter(item => item.kind === 'device').map(item => [item.key, item]));
      const loads = layers.devices && layers.loads ? (data[floor()] || []).map((control, index) => ({
        entity: control.entity,
        key: normalizeDevice(control.entity || ''),
        channel: channelNumber(control.entity),
        left: control.left,
        top: control.top,
        index
      })) : [];
      loads.forEach(load => {
        const device = devices.get(load.key);
        if (!device) return;
        const port = channelPortPx(device, load.channel);
        const fixture = (fixtures[floor()] || []).find(group => group.entity === load.entity);
        const endpoint = fixture?.points[0] || load;
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.classList.add('channel-wire');
        line.setAttribute('x1', port.x);
        line.setAttribute('y1', port.y);
        line.setAttribute('x2', parsePct(endpoint.left) * width / 100);
        line.setAttribute('y2', parsePct(endpoint.top) * height / 100);
        wires.appendChild(line);
      });
      if (layers.panels && layers.devices && layers.electric) {
        for (const [key, feed] of Object.entries(data.infrastructure.feeds)) {
          const device = devices.get(key), panel = data.infrastructure.panels[feed.panel];
          if (!device || !panel || panel.floor !== floor()) continue;
          const circuit = panel.circuits.find(c => String(c.number) === String(feed.circuit));
          const phase = feed.phase || circuit?.phase || '';
          const line = document.createElementNS('http://www.w3.org/2000/svg', 'line'); line.classList.add('feed-wire');
          line.style.stroke = phaseColors[phase] || '#657076'; line.style.strokeWidth = '2';
          const start = markerCenterPx(panel), end = markerCenterPx(device);
          for (const [attr,value] of Object.entries({x1:start.x,y1:start.y,x2:end.x,y2:end.y})) line.setAttribute(attr,value);
          const title = document.createElementNS('http://www.w3.org/2000/svg','title');
          title.textContent = `${panel.title} · Circuito ${feed.circuit || 'nao cadastrado'} · ${phase || 'Fase nao cadastrada'} · ${device.title}`;
          line.appendChild(title); wires.appendChild(line);
        }
      }
    }

    function renderMesh() {
      const mesh = document.getElementById('mesh');
      mesh.replaceChildren();
      if (!layers.zigbee || floor() === 'eletrica') return;
      const width = stage.clientWidth, height = stage.clientHeight;
      mesh.setAttribute('viewBox', `0 0 ${width} ${height}`);
      const visible = new Set(itemsForFloor().filter(i => i.kind === 'device').map(i => i.key));
      for (const link of zigbee[floor()]?.links || []) {
        if (!visible.has(link.from) || !visible.has(link.to)) continue;
        const positions = data.device_positions[floor()];
        const start = positions[link.from], end = positions[link.to];
        if (!start || !end) continue;
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.style.stroke = link.color;
        for (const [name, value] of Object.entries({x1:parsePct(start.left)*width/100, y1:parsePct(start.top)*height/100, x2:parsePct(end.left)*width/100, y2:parsePct(end.top)*height/100, stroke:link.color, opacity:link.opacity})) line.setAttribute(name, value);
        if (link.dashed) line.setAttribute('stroke-dasharray', '5 7');
        const title = document.createElementNS('http://www.w3.org/2000/svg', 'title'); title.textContent = link.title;
        line.appendChild(title); mesh.appendChild(line);
      }
    }

    function updateNetworkInfo(marker, item) {
      const network = item.network;
      if ((!layers.zigbee && !layers.signal) || !network?.is_zigbee) return;
      const values = Object.entries(network.signals).map(([kind, entity]) => {
        const state = hass()?.states[entity];
        const unit = kind === 'rssi' ? ` ${state?.attributes.unit_of_measurement || 'dBm'}` : '';
        return `${kind.toUpperCase()} ${state?.state || 'Indisponivel'}${unit}`;
      });
      const info = marker.querySelector('.network-info');
      if (info) info.textContent = values.join(' · ') || 'Sem leitura';
      marker.title = `${item.title} · ${network.network}${values.length ? ' · ' + values.join(' · ') : ''}`;
      if (layers.signal) {
        marker.classList.remove('signal-good','signal-fair','signal-poor','signal-unknown');
        const measurements = Object.entries(network.signals).map(([kind,entity]) => {
          const state = hass()?.states[entity]; const value = Number(state?.state);
          if (!state || !Number.isFinite(value) || !String(state.state).trim()) return null;
          return {kind,value,updated:state.last_updated};
        }).filter(Boolean);
        const measurement = measurements.find(m => m.kind === 'lqi') || measurements.find(m => m.kind === 'rssi');
        const quality = !measurement ? 'unknown' : measurement.kind === 'lqi'
          ? (measurement.value >= 100 ? 'good' : measurement.value >= 50 ? 'fair' : 'poor')
          : (measurement.value >= -70 ? 'good' : measurement.value >= -85 ? 'fair' : 'poor');
        marker.classList.add('signal-' + quality);
        marker.title += measurement ? ` · Leitura ${new Date(measurement.updated).toLocaleString('pt-BR')} · Classificacao indicativa (${measurement.kind.toUpperCase()})` : ' · Sem leitura de sinal';
      }
    }

    function render() {
      const currentFloor = floor();
      map.src = `assets/${images[currentFloor]}`;
      map.alt = `Mapa ${currentFloor}`;
      renderHaLayers();
      renderFixtures();

      stage.querySelectorAll('.marker').forEach(node => node.remove());
      document.getElementById('signalLegend').style.display = layers.signal && currentFloor !== 'eletrica' ? 'flex' : 'none';
      const items = currentFloor !== 'eletrica' ? itemsForFloor() : [];
      if (selected && !items.some(item => markerId(item) === markerId(selected))) {
        selected = null; selectedTitle.textContent = 'Selecione um ponto';
        leftField.disabled = true; topField.disabled = true; leftField.value = ''; topField.value = '';
      }

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
        marker.title = (isGirierImage ? `${item.model} ${channelText} · ${item.title}` : (isPhotoDevice || isModule ? `${item.model} · ${item.title}` : item.title)) + (item.positionPending ? ' · Posicao a confirmar' : '');
        marker.setAttribute('aria-label', marker.title);
        marker.dataset.id = markerId(item);
        if (item.kind === 'panel') {
          marker.innerHTML = `<img src="assets/${escapeHtml(item.image)}" alt="">`;
        } else if (isBreakerImage) {
          marker.innerHTML = '<img class="device-photo" src="assets/tongou-breaker-to-q-sy2-jzt.webp?v=1" alt="">';
        } else if (isGirierImage) {
          const channelItems = item.channels.map(channel => {
            const names = (item.channelNames?.[channel] || [`Canal ${channel}`]).join(' / ');
            return { channel, names };
          });
          marker.innerHTML = `<img class="device-photo" src="assets/girier-${item.channels.length}ch-module.png?v=4" alt="">` +
            channelItems
              .map(({ channel, names }) => {
                return `<span class="channel" data-channel="${channel}" title="Canal ${channel}: ${escapeHtml(names)}"><span class="channel-label">Canal ${channel}: ${escapeHtml(names)}</span></span>`;
              })
              .join('') +
            `<span class="channel-list">${channelItems.map(({ channel, names }) => `<span data-entity="${escapeHtml((data[floor()] || []).find(c => normalizeDevice(c.entity) === item.key && channelNumber(c.entity) === channel)?.entity || '')}" tabindex="0" role="button">${channel}: ${escapeHtml(names)}</span>`).join('')}</span>`;
        } else if (isModule) {
          marker.innerHTML = `<span class="module-count">${item.channels.length}</span>` +
            item.channels
              .map(channel => `<span class="channel" data-channel="${channel}" title="Canal ${channel}"></span>`)
              .join('');
        } else {
          marker.innerHTML = item.kind === 'device' ? '<span class="device-symbol"></span>' : '<span>L</span>';
          if (item.kind === 'load') marker.innerHTML = iconHtml(item.icon || 'mdi:lightbulb');
          else if (item.network?.icon && !item.entity?.startsWith('switch.')) marker.innerHTML = iconHtml(item.network.icon);
          if (item.deviceClass) {
            const symbol = {door:'door-open', window:'panels-top-left', opening:'door-open', motion:'scan-eye', occupancy:'scan-eye', moisture:'droplets', smoke:'flame', gas:'wind'}[item.deviceClass];
            marker.innerHTML = `<i data-lucide="${symbol}"></i>`;
            marker.style.background = '#287a65';
          }
        }
        if ((layers.zigbee || layers.signal) && item.network?.is_zigbee) {
          marker.style.setProperty('--network-color', item.network.network === 'Sonoff/ZHA' ? '#0c8cab' : '#c16232');
          const badge = document.createElement('span'); badge.className = 'network-badge'; badge.textContent = 'Z';
          marker.append(badge);
          if (layers.signal) { const info = document.createElement('span'); info.className = 'network-info'; marker.append(info); }
          updateNetworkInfo(marker, item);
        }
        if (selected && markerId(selected) === markerId(item)) marker.classList.add('selected');
        marker.addEventListener('pointerdown', event => {
          const channel = event.target.closest('.channel-list span');
          if (channel && !editing) { event.preventDefault(); toggleEntity(channel.dataset.entity); return; }
          if (!editing) {
            event.preventDefault();
            if (item.kind === 'panel') openPanel(item.key);
            else if (item.kind === 'load') toggleEntity(item.entity);
            else if (!isPhotoDevice && !isModule && item.entity && !item.key.startsWith('zigbee:')) moreInfo(item.entity);
            else { selected = isExpanded ? null : item; render(); }
            return;
          }
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
        marker.addEventListener('keydown', event => {
          if (event.key !== 'Enter' && event.key !== ' ') return;
          event.preventDefault();
          const channel = event.target.closest('.channel-list span');
          if (channel && !editing) toggleEntity(channel.dataset.entity);
          else if (editing) selectItem(item);
          else if (item.kind === 'panel') openPanel(item.key);
          else if (item.kind === 'load') toggleEntity(item.entity);
          else if (!isPhotoDevice && !isModule && item.entity && !item.key.startsWith('zigbee:')) moreInfo(item.entity);
          else { selected = isExpanded ? null : item; render(); }
        });
        stage.appendChild(marker);
      });
      renderWires(items);
      renderMesh();
      lucide.createIcons();

      list.innerHTML = '';
      items.forEach(item => {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'row';
        if (selected && markerId(selected) === markerId(item)) row.classList.add('selected');
        const subtitle = item.kind === 'panel' ? 'Quadro eletrico · Deposito' : item.kind === 'device'
          ? `${item.model || 'Modulo Zigbee'}${item.channels ? ` · ${item.channels.length === 1 ? '1 canal' : `${item.channels.length} canais`}` : ''}`
          : `Canal ${item.channel} · ${item.entity}`;
        row.innerHTML = `<strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(subtitle)}</span>`;
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
      renderFeedFields();
    }

    window.addEventListener('pointermove', event => {
      if (!editing) return;
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
    document.getElementById('edit').addEventListener('click', () => {
      editing = !editing;
      document.body.classList.toggle('editing', editing);
      document.getElementById('edit').setAttribute('aria-pressed', String(editing));
      if (floor() === 'eletrica' && editing) floorSelect.value = 'terreo';
      selected = null;
      dragging = null;
      render();
    });
    for (const [key, id] of Object.entries({devices:'devicesLayer', loads:'loadsLayer', panels:'panelsLayer', zigbee:'zigbeeLayer', signal:'signalLayer', electric:'electricLayer'})) {
      document.getElementById(id).addEventListener('click', () => {
        layers[key] = !layers[key];
        document.getElementById(id).setAttribute('aria-pressed', String(layers[key]));
        render();
      });
    }
    map.addEventListener('load', () => { renderWires(itemsForFloor()); renderMesh(); });
    window.addEventListener('resize', render);

    async function persistControls() {
      status.textContent = 'Salvando...';
      const response = await fetch('api/controls', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      });
      if (!response.ok) {
        status.textContent = 'Erro ao salvar';
        throw new Error('Erro ao salvar');
      }
      data = await response.json();
      status.textContent = 'Salvo';
      render();
    }
    document.getElementById('save').addEventListener('click', async () => { try { await persistControls(); } catch { status.textContent = 'Erro ao salvar'; } });
    new ResizeObserver(entries => document.documentElement.style.setProperty('--header-height', entries[0].contentRect.height + 'px')).observe(document.querySelector('header'));

    async function boot() {
      lucide.createIcons();
      const response = await fetch('api/controls');
      const payload = await response.json();
      data = payload.controls;
      images = payload.images;
      dashboard = payload.dashboard;
      zigbee = payload.zigbee;
      fixtures = payload.fixtures || {};
      channelNames = payload.channel_names || {};
      icons = await (await fetch('assets/mdi-icons.json')).json();
      if (!hass()) document.getElementById('connection').textContent = 'As visoes ao vivo ficam disponiveis dentro do Home Assistant.';
      Object.keys(images).forEach(name => {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name === 'terreo' ? 'Terreo' : 'Superior';
        floorSelect.appendChild(option);
      });
      const option = document.createElement('option');
      option.value = 'eletrica';
      option.textContent = 'Eletrica';
      floorSelect.appendChild(option);
      status.textContent = 'Pronto';
      render();
      refreshNames();
      setInterval(() => {
        document.querySelectorAll('#panelCircuits td[data-entity]').forEach(td => { if (td.dataset.entity) td.textContent = entityValue(td.dataset.entity); });
        updateFixtures();
        haCards.forEach(update => update());
        const items = itemsForFloor();
        stage.querySelectorAll('.marker').forEach(marker => {
          const item = items.find(item => markerId(item) === marker.dataset.id);
          if (item) updateNetworkInfo(marker, item);
        });
        stage.querySelectorAll('.marker.load').forEach(marker => {
          const item = itemsForFloor().find(item => markerId(item) === marker.dataset.id);
          marker.classList.toggle('on', hass()?.states[item?.entity]?.state === 'on');
        });
      }, 1000);
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
            discover_sensors(controls)
            dashboard = dashboard_config()
            network = zigbee_devices(controls, dashboard)
            self.send_json(200, {"controls": controls, "images": floor_images(), "dashboard": dashboard, "zigbee": network,
                                 "fixtures": json.loads((ASSETS / "floor-fixtures.json").read_text()),
                                 "channel_names": tuya_names.read_cache(DATA_DIR).get("names", {})})
            return
        if path.startswith("/assets/"):
            name = Path(path.removeprefix("/assets/")).name
            file_path = ASSETS / name
            current_asset = HA_CONFIG / "www" / "casa3d" / name
            if current_asset.is_file():
                file_path = current_asset
            private_asset = DATA_DIR / "assets" / name
            if private_asset.is_file():
                file_path = private_asset
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
            current_asset = HA_CONFIG / "www" / "casa3d" / name
            if current_asset.is_file():
                file_path = current_asset
            private_asset = DATA_DIR / "assets" / name
            if private_asset.is_file():
                file_path = private_asset
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404)
                return
            self.send_asset_headers(200, file_path, file_path.stat().st_size)
            return
        self.send_error(404)

    def do_POST(self):
        if self.path == "/api/channel-names":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                self.send_json(200, tuya_names.refresh(HA_CONFIG, DATA_DIR, load_controls(), force=bool(payload.get("force"))))
            except (ValueError, TypeError):
                self.send_json(400, {"error": "Pedido invalido."})
            return
        if self.path != "/api/controls":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length))
            save_controls(payload)
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
