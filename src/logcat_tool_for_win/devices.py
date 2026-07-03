from __future__ import annotations

from logcat_tool_for_win.models import DeviceInfo


def parse_devices_output(output: str) -> list[DeviceInfo]:
    devices: list[DeviceInfo] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if (
            not line
            or line.startswith("* daemon ")
            or line.startswith("List of devices attached")
        ):
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        serial = parts[0]
        state = parts[1]
        attrs: dict[str, str] = {}
        for token in parts[2:]:
            if ":" in token:
                key, value = token.split(":", 1)
                attrs[key] = value

        model = attrs.get("model", "")
        product = attrs.get("product", "")
        display_name = model or product or serial
        transport = "tcp" if ":" in serial else "usb"

        devices.append(
            DeviceInfo(
                serial=serial,
                display_name=display_name,
                transport=transport,
                state=state,
                model=model,
                product=product,
                raw_descriptor=line,
            )
        )

    return devices


def device_label(device: DeviceInfo) -> str:
    if device.display_name != device.serial:
        return f"{device.display_name} [{device.transport}] ({device.serial})"
    return f"{device.display_name} [{device.transport}]"
