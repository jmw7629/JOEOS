"""Wearable Simulator for the JoeOS Wearable Platform.

Provides isolated simulated development devices and adapters with deterministic
fixtures. Simulator state never enters production device state; it uses
separately prefixed identities and is always marked as a simulator.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Sequence, Tuple

from .devices import AdapterRegistry, DeviceRegistry

SIMULATOR_ADAPTER = "simulator.basic-glasses"
SIMULATOR_PREFIX = "sim_"

CAPABILITY_PROFILES: Dict[str, Tuple[str, ...]] = {
    "one_line_monochrome": ("display.text_card", "input.button", "connectivity.simulator"),
    "small_monocular": ("display.text_card", "display.grayscale", "input.button", "input.touch", "sensor.battery", "connectivity.simulator"),
    "color_card_display": ("display.text_card", "display.rich_card", "display.color", "input.button", "input.touch", "sensor.battery", "connectivity.simulator"),
    "audio_only": ("audio.output_tone", "audio.output_speech", "audio.input_push_to_talk", "input.button", "sensor.battery", "connectivity.simulator"),
    "display_plus_microphone": ("display.text_card", "display.color", "audio.input_push_to_talk", "input.button", "sensor.battery", "connectivity.simulator"),
    "display_plus_camera": ("display.text_card", "display.color", "camera.still_image", "camera.qr_scan", "input.button", "sensor.battery", "connectivity.simulator"),
    "low_bandwidth_relay": ("display.text_card", "input.button", "connectivity.simulator"),
    "low_battery_device": ("display.text_card", "input.button", "sensor.battery", "connectivity.simulator"),
    "privacy_mode_device": ("display.text_card", "display.rich_card", "input.button", "connectivity.simulator"),
}


class WearableSimulator:
    """Isolated, deterministic development devices (never production state)."""

    def __init__(self, devices: DeviceRegistry, adapters: AdapterRegistry) -> None:
        self._devices = devices
        self._adapters = adapters
        self._counter = 0

    def ensure_adapter(self) -> str:
        self._adapters.register(
            adapter_id=SIMULATOR_ADAPTER,
            display_name="Simulator Basic Glasses Adapter",
            plugin_id="joeos.simulator",
            supported_manufacturers=("joeos-simulator",),
            supported_transports=("simulator",),
            supports_discovery=True,
            supports_pairing=True,
            supported_capabilities=(
                "display.text_card",
                "display.rich_card",
                "display.color",
                "display.grayscale",
                "audio.output_tone",
                "audio.output_speech",
                "audio.input_push_to_talk",
                "camera.still_image",
                "camera.qr_scan",
                "input.button",
                "input.touch",
                "sensor.battery",
                "sensor.temperature",
                "connectivity.simulator",
            ),
            version="1.0.0",
            is_simulator=True,
        )
        return SIMULATOR_ADAPTER

    def create_device(self, *, profile: str, display_name: str = "Simulated Glasses") -> str:
        if profile not in CAPABILITY_PROFILES:
            raise ValueError("unknown simulator profile %r." % profile)
        self.ensure_adapter()
        self._counter += 1
        import uuid
        device_id = "sim_dev_%d_%s" % (self._counter, uuid.uuid4().hex[:6])
        self._devices.register(
            device_id=device_id,
            device_type="simulated_development_device",
            display_name=display_name,
            adapter_id=SIMULATOR_ADAPTER,
            plugin_id="joeos.simulator",
            transport="simulator",
            manufacturer="joeos-simulator",
            model=profile,
            firmware_version="sim-1.0.0",
        )
        capabilities = CAPABILITY_PROFILES[profile]
        for capability in capabilities:
            self._devices.set_capability(
                device_id=device_id, capability_id=capability, support_state="available", verification_state="verified"
            )
        self._devices.update_state(device_id, verified_capabilities=capabilities)
        return device_id

    def fixture_code(self, challenge_id: str) -> str:
        """Deterministic fixture pairing code for the simulator."""
        return "123456"

    def is_simulator_device(self, device_id: str) -> bool:
        return device_id.startswith(SIMULATOR_PREFIX)

    def list_profiles(self) -> Tuple[dict, ...]:
        return tuple({"profile": name, "capabilities": tuple(caps)} for name, caps in sorted(CAPABILITY_PROFILES.items()))