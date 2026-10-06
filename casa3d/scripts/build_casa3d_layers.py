import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("/tmp/lovelace-casa3d-latest.json")
CONTROLS = ROOT / "casa3d-controls.json"
OUTPUT = ROOT / "assets" / "lovelace.lovelace_casa_3d.layers.json"
TERREO_IMAGE = "/local/casa3d/casa_3d_terreo-v18.webp"
MESH_SVG_NAME = "zigbee-mesh-terreo-clean.svg"
MESH_SVG = ROOT / "assets" / MESH_SVG_NAME
WIRE_SVG_NAMES = {
    "terreo": "casa3d-wires-terreo.svg",
    "superior": "casa3d-wires-superior.svg",
}
FLOOR_SIZES = {
    "terreo": (962, 1635),
    "superior": (800, 1394),
}
ELECTRICAL_VIEW_TEMPLATE = ROOT / "assets" / "casa3d-electrical-view.json"
TUYA_SIGNAL_MAP = ROOT / "assets" / "tuya-signal-map.json"
BREAKER_DEVICE = "switch.tz3000_cayepv1a_ts011f"

SONOFF_COLOR = "#38bdf8"
TUYA_COLOR = "#fb923c"
GOOD_SIGNAL = "#22c55e"
UNKNOWN_SIGNAL = "#94a3b8"


def pct(value):
    return float(value.rstrip("%"))


def style(left, top, color, scale="0.72", z=5):
    return {
        "left": f"{left:g}%",
        "top": f"{top:g}%",
        "transform": f"translate(-50%, -50%) scale({scale})",
        "color": color,
        "background": "rgba(18, 22, 26, 0.86)",
        "border-radius": "50%",
        "padding": "4px",
        "box-shadow": "0 1px 4px rgba(0, 0, 0, 0.78)",
        "z-index": z,
    }


def signal_style(left, top, color, scale="0.86", z=5):
    return {
        "left": f"{left:g}%",
        "top": f"{top:g}%",
        "transform": f"translate(-50%, -50%) scale({scale})",
        "color": color,
        "background": "rgba(10, 14, 18, 0.64)",
        "border-radius": "50%",
        "padding": "3px",
        "box-shadow": "0 1px 3px rgba(0, 0, 0, 0.56)",
        "z-index": z,
    }


def toolbar_button(entity, icon, title, top):
    return {
        "type": "state-icon",
        "entity": entity,
        "icon": icon,
        "title": title,
        "state_color": True,
        "tap_action": {"action": "toggle"},
        "hold_action": {"action": "more-info"},
        "style": style(96, top, "#dce7ef", scale="0.88", z=10),
    }


def normalize_device(entity):
    key = re.sub(r"_switch_[1-4](?:_2)?$", "", entity)
    key = re.sub(r"_socket_1$", "", key)
    return key


def control_element(control):
    entity = control["entity"]
    tap_action = {"action": "more-info"} if entity.startswith("climate.") else {"action": "toggle"}
    return {
        "type": "state-icon",
        "entity": entity,
        "title": control["title"],
        "icon": control["icon"],
        "state_color": True,
        "tap_action": tap_action,
        "hold_action": {"action": "more-info"},
        "style": {
            "left": control["left"],
            "top": control["top"],
            "transform": "translate(-50%, -50%) scale(0.9)",
            "background": "rgba(20, 24, 28, 0.68)",
            "border-radius": "50%",
            "padding": "3px",
            "box-shadow": "0 1px 3px rgba(0, 0, 0, 0.7)",
            "z-index": 3,
        },
    }


def channel_number(entity):
    match = re.search(r"_switch_([1-4])(?:_2)?$", entity)
    return int(match.group(1)) if match else 1


def grouped_controls(controls, floor):
    groups = defaultdict(list)
    for control in floor_controls(controls, floor):
        entity = control["entity"]
        if entity.startswith("climate."):
            continue
        groups[normalize_device(entity)].append(control)
    return groups


def device_title(group):
    title = group[0]["title"]
    return re.sub(r"\s+[1-4]$", "", title)


def physical_device_element(key, group, left, top, title):
    channels = sorted({channel_number(item["entity"]) for item in group})
    channel_count = max(1, min(4, len(channels)))
    is_breaker = key == BREAKER_DEVICE
    image = (
        "tongou-breaker-to-q-sy2-jzt.webp"
        if is_breaker
        else f"girier-{channel_count}ch-module.png"
    )
    model = "Disjuntor Zigbee" if is_breaker else "Girier"
    channel_text = "1 canal" if channel_count == 1 else f"{channel_count} canais"
    width = "2.0%" if is_breaker else "3.0%"
    return {
        "type": "image",
        "entity": group[0]["entity"],
        "image": f"/local/casa3d/{image}",
        "title": f"{model} · {title} · {channel_text}",
        "tap_action": {"action": "more-info"},
        "hold_action": {"action": "more-info"},
        "style": {
            "left": f"{left:g}%",
            "top": f"{top:g}%",
            "width": width,
            "transform": "translate(-50%, -50%)",
            "filter": "drop-shadow(0 1px 3px rgba(0,0,0,0.55))",
            "z-index": 4,
        },
    }


def physical_device_elements(controls, floor):
    elements = []
    for key, group in grouped_controls(controls, floor).items():
        left, top, positioned_title = device_point(controls, floor, key, group)
        title = positioned_title or device_title(group)
        if key.startswith("switch."):
            elements.append(physical_device_element(key, group, left, top, title))
    return elements


def channel_rel_x(channel_count, channel):
    positions = {
        1: {1: 0.50},
        2: {1: 0.43, 2: 0.57},
        3: {1: 0.37, 2: 0.50, 3: 0.63},
        4: {1: 0.30, 2: 0.43, 3: 0.56, 4: 0.69},
    }
    return positions.get(channel_count, positions[1]).get(channel, 0.50)


def wire_start_point(left, top, channel, channel_count, width, height):
    photo_width_pct = 3.0
    photo_aspect = 260 / 303
    rel_x = channel_rel_x(channel_count, channel)
    x = left + (rel_x - 0.50) * photo_width_pct
    y = top + 0.31 * (photo_width_pct * width * photo_aspect / height * 100)
    return x, y


def write_wire_svg(controls, floor):
    width, height = FLOOR_SIZES[floor]
    groups = grouped_controls(controls, floor)
    paths = []
    for key, group in groups.items():
        if not key.startswith("switch."):
            continue
        left, top, positioned_title = device_point(controls, floor, key, group)
        title = positioned_title or device_title(group)
        channels = sorted({channel_number(item["entity"]) for item in group})
        channel_count = max(1, min(4, len(channels)))
        for control in group:
            channel = channel_number(control["entity"])
            x1_pct, y1_pct = wire_start_point(left, top, channel, channel_count, width, height)
            x1 = x1_pct * width / 100
            y1 = y1_pct * height / 100
            x2 = pct_to_svg(control["left"], width)
            y2 = pct_to_svg(control["top"], height)
            paths.append(
                svg_path(
                    x1,
                    y1,
                    x2,
                    y2,
                    "#256b8f",
                    2.1,
                    0.46,
                    f"{title} canal {channel} para {control['title']}",
                    dash="7 9",
                )
            )
    output = ROOT / "assets" / WIRE_SVG_NAMES[floor]
    output.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        'preserveAspectRatio="none">\n'
        '  <g fill="none" stroke-linecap="round">\n'
        + "\n".join(paths)
        + "\n  </g>\n"
        "</svg>\n"
    )


def wire_overlay(floor):
    return {
        "type": "image",
        "image": f"/local/casa3d/{WIRE_SVG_NAMES[floor]}",
        "title": "Fios entre switches físicos e lâmpadas",
        "tap_action": {"action": "none"},
        "hold_action": {"action": "none"},
        "style": {
            "left": "50%",
            "top": "50%",
            "width": "100%",
            "height": "100%",
            "transform": "translate(-50%, -50%)",
            "pointer-events": "none",
            "z-index": 2,
        },
    }


def floor_controls(controls, floor):
    return controls.get(floor, [])


def device_positions(controls, floor):
    return controls.get("device_positions", {}).get(floor, {})


def device_point(controls, floor, key, group):
    positions = device_positions(controls, floor)
    if key in positions:
        point = positions[key]
        return pct(point["left"]), pct(point["top"]), point.get("title")

    left = sum(pct(item["left"]) for item in group) / len(group)
    top = sum(pct(item["top"]) for item in group) / len(group)
    return left, top, None


def lqi_label(entity, left, top, title):
    return {
        "type": "state-label",
        "entity": entity,
        "prefix": "LQI ",
        "title": f"Qualidade do sinal · {title}",
        "tap_action": {"action": "more-info"},
        "hold_action": {"action": "more-info"},
        "style": {
            "left": f"{left:g}%",
            "top": f"{top + 2.4:g}%",
            "transform": "translate(-50%, -50%)",
            "color": "#ffffff",
            "background": "rgba(10, 14, 18, 0.82)",
            "border-radius": "4px",
            "padding": "1px 4px",
            "font-size": "9px",
            "line-height": "13px",
            "white-space": "nowrap",
            "z-index": 6,
        },
    }


def tuya_nodes(controls, floor, signal_map):
    groups = defaultdict(list)
    for control in floor_controls(controls, floor):
        entity = control["entity"]
        if entity.startswith("climate."):
            continue
        if "zbeacon_ts011f" in entity or "tz3000_cayepv1a" in entity:
            continue
        groups[normalize_device(entity)].append(control)

    nodes = []
    for key, group in groups.items():
        left, top, positioned_title = device_point(controls, floor, key, group)
        title = positioned_title or re.sub(r"\s+[1-4]$", "", group[0]["title"])
        mapped = next(
            (
                signal_map[item["entity"]]
                for item in group
                if item["entity"] in signal_map
            ),
            None,
        )
        device_name = mapped["name"] if mapped else title
        nodes.append(
            {
                "type": "state-icon",
                "entity": group[0]["entity"],
                "icon": "mdi:wifi-strength-alert-outline",
                "title": f"X5/Tuya · {device_name}",
                "state_color": False,
                "tap_action": {"action": "more-info"},
                "hold_action": {"action": "more-info"},
                "style": signal_style(left, top, TUYA_COLOR),
            }
        )
        if mapped:
            nodes.append(lqi_label(mapped["sensor"], left, top, device_name))
    return nodes


def pct_to_svg(value, axis):
    return pct(value) * axis / 100


def grouped_tuya_points(controls, floor):
    groups = defaultdict(list)
    for control in floor_controls(controls, floor):
        entity = control["entity"]
        if entity.startswith("climate."):
            continue
        if "zbeacon_ts011f" in entity or "tz3000_cayepv1a" in entity:
            continue
        groups[normalize_device(entity)].append(control)

    points = []
    positions = device_positions(controls, floor)
    for key, group in groups.items():
        if key in positions:
            point = positions[key]
            x = pct_to_svg(point["left"], 962)
            y = pct_to_svg(point["top"], 1635)
            title = point.get("title") or re.sub(r"\s+[1-4]$", "", group[0]["title"])
        else:
            x = sum(pct_to_svg(item["left"], 962) for item in group) / len(group)
            y = sum(pct_to_svg(item["top"], 1635) for item in group) / len(group)
            title = re.sub(r"\s+[1-4]$", "", group[0]["title"])
        points.append((x, y, title))
    return points


def svg_path(x1, y1, x2, y2, color, width, opacity, title, dash=None):
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'    <path d="M{x1:g} {y1:g} L{x2:g} {y2:g}" stroke="{color}" '
        f'stroke-width="{width}" opacity="{opacity}"{dash_attr}>\n'
        f"      <title>{title}</title>\n"
        "    </path>"
    )


def write_mesh_svg(controls):
    sonoff = (231, 801)
    tuya = (279, 801)
    paths = [
        svg_path(
            *sonoff,
            154,
            687,
            SONOFF_COLOR,
            5,
            0.82,
            "Sonoff/ZHA para Geladeira Branca: rota direta observada",
        ),
        svg_path(
            *sonoff,
            798,
            441,
            SONOFF_COLOR,
            5,
            0.82,
            "Sonoff/ZHA para Aquecedor: rota direta observada",
        ),
        svg_path(
            154,
            687,
            798,
            441,
            "#7dd3fc",
            3,
            0.52,
            "Geladeira Branca e Aquecedor: vizinhanca observada",
        ),
        svg_path(
            *sonoff,
            269,
            752,
            "#fb7185",
            4,
            0.86,
            "Medidor Quadro Direito: sem vizinhanca recente",
            dash="12 9",
        ),
    ]
    for x, y, title in grouped_tuya_points(controls, "terreo"):
        paths.append(
            svg_path(
                *tuya,
                x,
                y,
                TUYA_COLOR,
                2.4,
                0.28,
                f"X5/Tuya para {title}: rota estimada pelo gateway",
                dash="8 11",
            )
        )

    MESH_SVG.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 962 1635" '
        'preserveAspectRatio="none">\n'
        '  <g fill="none" stroke-linecap="round">\n'
        + "\n".join(paths)
        + "\n  </g>\n"
        "</svg>\n"
    )


def mesh_overlay():
    return {
        "type": "image",
        "image": f"/local/casa3d/{MESH_SVG_NAME}",
        "title": "Malha ZHA observada pelo coordenador Sonoff",
        "tap_action": {"action": "none"},
        "hold_action": {"action": "none"},
        "style": {
            "left": "50%",
            "top": "50%",
            "width": "100%",
            "height": "100%",
            "transform": "translate(-50%, -50%)",
            "pointer-events": "none",
            "z-index": 4,
        },
    }


def zha_nodes():
    nodes = [
        {
            "type": "icon",
            "icon": "mdi:access-point-network",
            "title": "Coordenador ZHA · SONOFF ZBDongle-E",
            "tap_action": {
                "action": "navigate",
                "navigation_path": "/config/devices/device/952e3f936690b833d549e0e362ed3e65",
            },
            "hold_action": {"action": "none"},
            "style": style(24, 49, SONOFF_COLOR, scale="0.9"),
        },
        {
            "type": "icon",
            "icon": "mdi:access-point-network",
            "title": "Gateway X5/Tuya",
            "tap_action": {
                "action": "navigate",
                "navigation_path": "/config/devices/device/ea964d4659192e0ca200a70a16fdf1b7",
            },
            "hold_action": {"action": "none"},
            "style": style(29, 49, TUYA_COLOR, scale="0.9"),
        },
    ]

    devices = [
        (
            "sensor.zbeacon_ts011f_lqi",
            "sensor.zbeacon_ts011f_rssi",
            "Geladeira Branca",
            "e0252dfcdb7b6fedbd8627eb0fcc034a",
            16,
            42,
            "mdi:wifi-strength-4",
            GOOD_SIGNAL,
        ),
        (
            "sensor.tz3000_cayepv1a_ts011f_lqi",
            "sensor.tz3000_cayepv1a_ts011f_rssi",
            "Aquecedor",
            "0e9083558fb6cfdeeb0aca2a099c66d9",
            83,
            27,
            "mdi:wifi-strength-alert-outline",
            UNKNOWN_SIGNAL,
        ),
        (
            "sensor.medidor_quadro_direito_lqi",
            "sensor.medidor_quadro_direito_rssi",
            "Medidor Quadro Direito",
            "88ac5763d7b6c354c4e9666c9faf39d9",
            28,
            46,
            "mdi:wifi-strength-alert-outline",
            UNKNOWN_SIGNAL,
        ),
    ]
    for lqi, rssi, name, device_id, left, top, icon, color in devices:
        nodes.append(
            {
                "type": "state-icon",
                "entity": lqi,
                "icon": icon,
                "title": f"Sonoff/ZHA · {name}",
                "state_color": False,
                "tap_action": {"action": "more-info"},
                "hold_action": {
                    "action": "navigate",
                    "navigation_path": f"/config/devices/device/{device_id}",
                },
                "style": signal_style(left, top, color),
            }
        )
    return nodes


dashboard = json.loads(SOURCE.read_text())
controls = json.loads(CONTROLS.read_text())
tuya_signal_map = json.loads(TUYA_SIGNAL_MAP.read_text())
write_mesh_svg(controls)
for floor in WIRE_SVG_NAMES:
    write_wire_svg(controls, floor)


def electrical_elements_by_floor(views):
    result = {}
    electrical_view = next(
        (view for view in views if view.get("path") == "eletrica"), None
    )
    if not electrical_view:
        return result

    for section in electrical_view.get("sections", []):
        for card in section.get("cards", []):
            if card.get("type") != "picture-elements":
                continue
            image = card.get("image", "")
            if "terreo" in image:
                result["terreo"] = card.get("elements", [])
            elif "superior" in image:
                result["superior"] = card.get("elements", [])
    return result


views = dashboard["data"]["config"]["views"]
electrical_by_floor = electrical_elements_by_floor(views)
electrical_view = next(
    (view for view in views if view.get("path") == "eletrica"), None
)
if electrical_view:
    for section in electrical_view.get("sections", []):
        section["cards"] = [
            card
            for card in section.get("cards", [])
            if card.get("type") != "picture-elements"
            and card.get("heading") not in {"Mapa elétrico", "Térreo", "Superior"}
        ]
else:
    electrical_view = json.loads(ELECTRICAL_VIEW_TEMPLATE.read_text())

for view in views:
    if not isinstance(controls.get(view.get("path")), list) or not view.get("cards"):
        continue
    card = view["cards"][0]
    if view.get("path") == "terreo":
        card["image"] = TERREO_IMAGE
    elements = card.get("elements", [])
    device_layer_entity = "input_boolean.casa3d_mostrar_dispositivos"
    zigbee_layer_entity = "input_boolean.casa3d_mostrar_zigbee"
    electrical_layer_entity = "input_boolean.casa3d_mostrar_eletrica"
    device_elements = [wire_overlay(view["path"])] + physical_device_elements(controls, view["path"]) + [
        control_element(control) for control in floor_controls(controls, view["path"])
    ]
    existing_electrical_layer = next(
        (
            element
            for element in elements
            if element.get("type") == "conditional"
            and element.get("conditions", [{}])[0].get("entity")
            == electrical_layer_entity
        ),
        None,
    )
    electrical_elements = electrical_by_floor.get(view["path"], [])
    if existing_electrical_layer:
        electrical_elements = existing_electrical_layer.get("elements", [])

    def is_layer_element(element):
        if element.get("entity", "").startswith("input_boolean.casa3d_"):
            return True
        if element.get("title") == "Mapa elétrico":
            return True
        if element.get("type") != "conditional":
            return False
        entity = element.get("conditions", [{}])[0].get("entity")
        return entity in {
            device_layer_entity,
            zigbee_layer_entity,
            electrical_layer_entity,
        }

    base_elements = [element for element in elements if not is_layer_element(element)]

    device_layer = {
        "type": "conditional",
        "conditions": [
            {"entity": "input_boolean.casa3d_mostrar_dispositivos", "state": "on"}
        ],
        "elements": device_elements,
    }

    network_elements = tuya_nodes(controls, view["path"], tuya_signal_map)
    if view["path"] == "terreo":
        network_elements = [mesh_overlay()] + zha_nodes() + network_elements
    zigbee_layer = {
        "type": "conditional",
        "conditions": [
            {"entity": "input_boolean.casa3d_mostrar_zigbee", "state": "on"}
        ],
        "elements": network_elements,
    }
    electrical_layer = {
        "type": "conditional",
        "conditions": [
            {"entity": "input_boolean.casa3d_mostrar_eletrica", "state": "on"}
        ],
        "elements": electrical_elements,
    }

    card["elements"] = base_elements + [
        device_layer,
        zigbee_layer,
        electrical_layer,
        toolbar_button(
            "input_boolean.casa3d_mostrar_dispositivos",
            "mdi:light-switch",
            "Mostrar dispositivos",
            4,
        ),
        toolbar_button(
            "input_boolean.casa3d_mostrar_zigbee",
            "mdi:zigbee",
            "Mostrar rede Zigbee",
            8,
        ),
        toolbar_button(
            "input_boolean.casa3d_mostrar_eletrica",
            "mdi:transmission-tower",
            "Mostrar mapa elétrico",
            12,
        ),
    ]

dashboard["data"]["config"]["views"] = [
    view for view in views if view.get("path") != "eletrica"
] + [electrical_view]

OUTPUT.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n")
if TERREO_IMAGE not in OUTPUT.read_text():
    raise SystemExit(f"Imagem do térreo não preservada: esperado {TERREO_IMAGE}")
