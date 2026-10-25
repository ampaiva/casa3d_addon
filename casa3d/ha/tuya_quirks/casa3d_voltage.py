"""Correct decivolts/deciwatts advertised as whole volts/watts by 16A Estimate Socket.

Product vSu2uIvNamkj9gdG reports e.g. 1270 for 127.0 V, but declares
scale=0 for voltage and power (e.g. 8020 means 802 W). Use HA's supported persistent Tuya quirks, without patching core.
"""
from tuya_device_handlers import TUYA_QUIRKS_REGISTRY
from tuya_device_handlers.builder import DeviceQuirk
from tuya_device_handlers.const import DPMode

(
    DeviceQuirk()
    .applies_to(product_id='vSu2uIvNamkj9gdG')
    .add_dpid_integer(dpid=6, dpcode='cur_voltage', dpmode=DPMode.READ,
                      unit='V', min=0, max=2500, scale=1, step=1)
    .add_dpid_integer(dpid=5, dpcode='cur_power', dpmode=DPMode.READ,
                      unit='W', min=0, max=50000, scale=1, step=1)
    .register(TUYA_QUIRKS_REGISTRY)
)
