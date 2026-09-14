"""kernel/home/__init__.py — P4-C exports (J-14/J-16/J-17) + PJ-05 (research/13)."""
from kernel.home.ha import ha_risk_overrides, mount_home_assistant
from kernel.home.mqtt import MqttBridge, parse_frigate_event
from kernel.home.serial_bridge import (
    SerialBridge,
    SerialUnavailable,
    build_hardware_tool,
)

__all__ = ["MqttBridge", "SerialBridge", "SerialUnavailable",
           "build_hardware_tool", "ha_risk_overrides", "mount_home_assistant",
           "parse_frigate_event"]
