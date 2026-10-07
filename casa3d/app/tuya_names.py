"""Read Tuya channel aliases using the existing LocalTuya cloud account."""

import hashlib
import hmac
import json
import threading
import time
from urllib.request import Request, urlopen


LOCK = threading.Lock()
ENDPOINTS = {
    "us": "https://openapi.tuyaus.com",
    "eu": "https://openapi.tuyaeu.com",
    "cn": "https://openapi.tuyacn.com",
    "in": "https://openapi.tuyain.com",
    "ea": "https://openapi-ueaz.tuyaus.com",
    "we": "https://openapi-weaz.tuyaeu.com",
    "sg": "https://openapi-sg.iotbing.com",
}


class TuyaReader:
    def __init__(self, account):
        self.account = account
        self.token = ""

    def get(self, path):
        stamp = str(int(time.time() * 1000))
        content_hash = hashlib.sha256(b"").hexdigest()
        payload = self.account["client_id"] + self.token + stamp
        payload += "GET\n" + content_hash + "\n\n" + path
        signature = hmac.new(self.account["client_secret"].encode(), payload.encode(), hashlib.sha256).hexdigest().upper()
        request = Request(ENDPOINTS[self.account["region"]] + path, headers={
            "client_id": self.account["client_id"], "access_token": self.token,
            "sign": signature, "t": stamp, "sign_method": "HMAC-SHA256",
        })
        with urlopen(request, timeout=15) as response:
            result = json.load(response)
        if not result.get("success"):
            raise RuntimeError("Tuya recusou a consulta (codigo %s)." % result.get("code", "desconhecido"))
        return result["result"]

    def properties(self, device_id):
        if not self.token:
            self.token = self.get("/v1.0/token?grant_type=1")["access_token"]
        return self.get("/v2.0/cloud/thing/%s/shadow/properties" % device_id).get("properties", [])


def bindings(entities, accounts, wanted):
    result = {}
    for index, account in enumerate(accounts):
        for device_id in account.get("devices", {}):
            for entity in entities:
                entity_id = entity["entity_id"]
                if entity_id not in wanted:
                    continue
                unique = entity.get("unique_id", "")
                if entity.get("platform") == "localtuya" and unique.startswith("local_" + device_id + "_"):
                    result[entity_id] = (index, device_id, unique[len("local_" + device_id + "_"):], "dp_id")
                elif entity.get("platform") == "tuya" and unique.startswith("tuya." + device_id):
                    result[entity_id] = (index, device_id, unique[len("tuya." + device_id):], "code")
    return result


def read_cache(data_dir):
    path = data_dir / "tuya-channel-names.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"names": {}, "updated_at": 0}


def refresh(config_dir, data_dir, controls, force=False, reader_factory=TuyaReader):
    with LOCK:
        cache = read_cache(data_dir)
        if not force and time.time() - cache.get("updated_at", 0) < 3600:
            return cache
        try:
            storage = config_dir / ".storage"
            entries = json.loads((storage / "core.config_entries").read_text())["data"]["entries"]
            accounts = [entry["data"] for entry in entries if entry["domain"] == "localtuya"
                        and not entry["data"].get("no_cloud") and entry["data"].get("client_id")
                        and entry["data"].get("client_secret") and entry["data"].get("region") in ENDPOINTS]
            entities = json.loads((storage / "core.entity_registry").read_text())["data"]["entities"]
            wanted = {control["entity"] for floor in controls.values() if isinstance(floor, list) for control in floor}
            mapping = bindings(entities, accounts, wanted)
            if not mapping:
                return {**cache, "error": "Nenhuma conexao Tuya com acesso aos nomes foi encontrada."}
            readers = {}
            names = dict(cache.get("names", {}))
            errors = 0
            groups = {(account, device) for account, device, _, _ in mapping.values()}
            for account, device in sorted(groups):
                try:
                    reader = readers.setdefault(account, reader_factory(accounts[account]))
                    properties = reader.properties(device)
                    for entity, (idx, dev, channel, field) in mapping.items():
                        if (idx, dev) != (account, device):
                            continue
                        prop = next((p for p in properties if str(p.get(field)) == channel), None)
                        if prop is not None:
                            alias = (prop.get("custom_name") or "").strip()
                            if alias:
                                names[entity] = alias
                            else:
                                names.pop(entity, None)
                except Exception:
                    errors += 1
            result = {"names": names, "updated_at": time.time()}
            if errors:
                result["error"] = "Nao foi possivel atualizar %s dispositivo(s); nomes anteriores preservados." % errors
            data_dir.mkdir(parents=True, exist_ok=True)
            path = data_dir / "tuya-channel-names.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(result, ensure_ascii=False))
            temporary.replace(path)
            return result
        except Exception:
            return {**cache, "error": "Nao foi possivel consultar os nomes Tuya; nomes anteriores preservados."}
