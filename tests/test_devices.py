from logcat_tool_for_win.devices import parse_devices_output


def test_parse_devices_output_handles_usb_tcp_and_bad_states() -> None:
    output = """List of devices attached
R58M12345\tdevice usb:1-1 product:shiba model:Pixel_8 device:shiba transport_id:5
192.168.0.15:5555\tdevice product:husky model:Pixel_8_Pro transport_id:7
emulator-5554\toffline transport_id:9
ZX1G22ABC\tunauthorized usb:1-2 transport_id:11
"""

    devices = parse_devices_output(output)

    assert [device.serial for device in devices] == [
        "R58M12345",
        "192.168.0.15:5555",
        "emulator-5554",
        "ZX1G22ABC",
    ]
    assert devices[0].transport == "usb"
    assert devices[1].transport == "tcp"
    assert devices[2].state == "offline"
    assert devices[3].display_name == "ZX1G22ABC"
