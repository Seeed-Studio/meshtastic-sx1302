#!/usr/bin/env python3
"""Probe the SX1261, SX1302 and STTS751 of an SX130x gateway on SPI0/I2C1.

The default wiring is:

    SX1302 -> /dev/spidev0.0 (CS0)
    SX1261 -> /dev/spidev0.1 (CS1)
    STTS751 temperature sensor -> /dev/i2c-1 (0x39, 0x3b or 0x38)

The sx1302_hal requires the STTS751 on SPI concentrators (lgw_start() fails
when no sensor responds), so all three probes must pass for the script to
exit 0.

By default the probe only performs reads.  With ``--reset`` it applies the
board reset wiring before probing:

    SX1302 GPIO17: 1 -> 0
    SX1261 GPIO5:  0 -> 1

Requires the Linux ``spidev`` Python module and either ``smbus2`` or
``smbus``.  Run it on the target host with:

    tools/probe_sx1261_sx1302.py

Use ``--help`` to override the device paths or SPI speed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

try:
    import spidev
except ImportError:  # pragma: no cover - depends on the target host
    spidev = None  # type: ignore[assignment]

try:  # smbus2 is pure Python and is commonly used on Raspberry Pi hosts.
    import smbus2
except ImportError:  # pragma: no cover - depends on the target host
    smbus2 = None  # type: ignore[assignment]

try:
    import smbus
except ImportError:  # pragma: no cover - depends on the target host
    smbus = None  # type: ignore[assignment]


DEFAULT_SX1261_DEVICE = "/dev/spidev0.1"
DEFAULT_SX1302_DEVICE = "/dev/spidev0.0"
DEFAULT_SPEED_HZ = 2_000_000
DEFAULT_SX1302_RESET_GPIO = 17
DEFAULT_SX1261_RESET_GPIO = 5
DEFAULT_I2C_DEVICE = "/dev/i2c-1"
# Matches I2C_PORT_TEMP_SENSOR in the sx1302_hal (loragw_stts751.h).
DEFAULT_TEMP_SENSOR_ADDRESSES = (0x39, 0x3B, 0x38)

SX1261_GET_STATUS = 0xC0
SX1261_READ_REGISTER = 0x1D
SX1261_PRAM_VERSION_ADDRESS = 0x0320
SX1261_PRAM_VERSION_LENGTH = 15

SX1302_READ_TARGET = 0x00
SX1302_COMMON_VERSION_REGISTER = 0x5606
SX1302_COMMON_PAGE_REGISTER = 0x5600

STTS751_REG_TEMP_H = 0x00
STTS751_REG_STATUS = 0x01
STTS751_REG_TEMP_L = 0x02
STTS751_REG_PROD_ID = 0xFD
STTS751_REG_MAN_ID = 0xFE
STTS751_REG_REV_ID = 0xFF

STTS751_STATUS_TRIPT = 1 << 0
STTS751_STATUS_TRIPL = 1 << 5
STTS751_STATUS_TRIPH = 1 << 6

STTS751_PRODUCT_IDS = {
    0x00: "STTS751-0",
    0x01: "STTS751-1",
}
STTS751_EXPECTED_PRODUCT_BY_ADDRESS = {
    0x39: 0x00,  # STTS751-0DP3F
    0x3B: 0x01,  # STTS751-1DP3F
    0x38: 0x00,  # STTS751-0DP3F on full duplex CN490 ref design
}
STTS751_MANUFACTURER_ID = 0x53

SX1261_MODES = {
    0x00: "sleep",
    0x20: "standby RC",
    0x30: "standby XOSC",
    0x40: "frequency synthesis",
    0x50: "receive",
    0x60: "transmit",
}
SX1261_COMMAND_STATUSES = {
    0x02: "ready",
    0x04: "data available",
    0x06: "timeout",
    0x08: "processing error",
    0x0A: "execution failure",
    0x0C: "transmit done",
}


class SpiDevice(Protocol):
    mode: int
    bits_per_word: int
    max_speed_hz: int
    lsbfirst: bool

    def xfer2(self, values: Sequence[int]) -> list[int]:
        ...

    def close(self) -> None:
        ...


class I2CBus(Protocol):
    def read_byte_data(self, address: int, register: int) -> int:
        ...

    def close(self) -> None:
        ...


@dataclass
class ProbeResult:
    chip: str
    device: str
    ok: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    address: int | None = None


def _parse_spidev_path(path: str) -> tuple[int, int]:
    match = re.fullmatch(r"(?:/dev/)?spidev(\d+)\.(\d+)", path)
    if not match:
        raise ValueError(
            f"{path!r} is not a Linux spidev path (expected /dev/spidevBUS.CS)"
        )
    return int(match.group(1)), int(match.group(2))


def open_spi(path: str, speed_hz: int) -> SpiDevice:
    """Open and configure one Linux spidev node."""
    if spidev is None:
        raise RuntimeError(
            "Python module 'spidev' is required; install the host package "
            "(for example, python3-spidev)"
        )

    bus, chip_select = _parse_spidev_path(path)
    device = spidev.SpiDev()
    try:
        device.open(bus, chip_select)
        device.mode = 0
        device.bits_per_word = 8
        device.lsbfirst = False
        device.max_speed_hz = speed_hz
    except Exception:
        try:
            device.close()
        except Exception:
            pass
        raise
    return device


def _parse_i2c_path(path: str) -> int:
    """Return the Linux I2C bus number encoded in a device path."""
    match = re.fullmatch(r"(?:/dev/)?i2c-(\d+)", path)
    if not match:
        raise ValueError(
            f"{path!r} is not a Linux I2C device path "
            "(expected /dev/i2c-N)"
        )
    return int(match.group(1))


def _parse_i2c_address(value: str) -> int:
    try:
        address = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid I2C address {value!r} (use e.g. 0x39)"
        ) from exc
    if not 0 <= address <= 0x7F:
        raise argparse.ArgumentTypeError(
            f"I2C address 0x{address:x} is outside the 7-bit range 0x00..0x7f"
        )
    return address


def open_i2c(path: str) -> I2CBus:
    """Open a Linux I2C bus using smbus2 or python-smbus."""
    bus_type = smbus2 or smbus
    if bus_type is None:
        raise RuntimeError(
            "Python module 'smbus2' or 'smbus' is required; install "
            "smbus2 (or the host package python3-smbus)"
        )
    return bus_type.SMBus(_parse_i2c_path(path))


def _gpio_value_path(number: int) -> Path:
    return Path(f"/sys/class/gpio/gpio{number}/value")


class _GpiosetHold:
    """Keep a modern GPIO character-device line requested at its final value."""

    def __init__(self, number: int, first: int, gpiochip: int):
        self.number = number
        self.process = subprocess.Popen(
            [
                "gpioset",
                "--chip",
                str(gpiochip),
                "--toggle",
                "1ms,86400s",
                f"{number}={first}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.005)
        if self.process.poll() is not None:
            message = (self.process.stderr.read() if self.process.stderr else "").strip()
            raise OSError(f"failed to reset GPIO{number} with gpioset: {message or 'process exited'}")

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def reset_gpio(number: int, first: int, second: int, gpiochip: int) -> _GpiosetHold | None:
    """Apply a reset sequence through sysfs or modern libgpiod's gpioset."""
    value_path = _gpio_value_path(number)
    if value_path.exists():
        direction_path = value_path.parent / "direction"
        try:
            direction_path.write_text("out\n")
            value_path.write_text(f"{first}\n")
            time.sleep(0.001)
            value_path.write_text(f"{second}\n")
            time.sleep(0.010)
            return None
        except OSError as exc:
            raise OSError(f"failed to reset GPIO{number} through {value_path}: {exc}") from exc

    try:
        # gpioset's first toggle produces the requested first -> second edge;
        # the long second period keeps the final level asserted during SPI reads.
        return _GpiosetHold(number, first, gpiochip)
    except FileNotFoundError as exc:
        raise OSError(
            f"GPIO{number} is not exported and gpioset is unavailable; "
            "export it through /sys/class/gpio or install libgpiod"
        ) from exc
    except OSError:
        raise


def reset_bundled_radios(sx1302_gpio: int, sx1261_gpio: int, gpiochip: int) -> list[_GpiosetHold]:
    holds: list[_GpiosetHold] = []
    sx1302_hold = reset_gpio(sx1302_gpio, 1, 0, gpiochip)
    if sx1302_hold is not None:
        holds.append(sx1302_hold)
    try:
        sx1261_hold = reset_gpio(sx1261_gpio, 0, 1, gpiochip)
    except OSError:
        for hold in holds:
            hold.close()
        raise
    if sx1261_hold is not None:
        holds.append(sx1261_hold)
    return holds


def transfer(spi: SpiDevice, tx: Sequence[int]) -> bytes:
    """Perform one transaction while keeping chip select asserted."""
    response = spi.xfer2(list(tx))
    if len(response) != len(tx):
        raise OSError(
            f"short SPI transfer: sent {len(tx)} bytes, received {len(response)}"
        )
    return bytes(response)


def read_sx1261_register(spi: SpiDevice, address: int, length: int) -> bytes:
    # SX126x returns a status/dummy byte before the requested register bytes.
    frame = [
        SX1261_READ_REGISTER,
        (address >> 8) & 0xFF,
        address & 0xFF,
        0x00,
    ] + [0x00] * length
    return transfer(spi, frame)[4:]


def probe_sx1261(spi: SpiDevice, device: str) -> ProbeResult:
    status_frame = transfer(spi, [SX1261_GET_STATUS, 0x00])
    status = status_frame[1]
    mode = status & 0x70
    command_status = status & 0x0E

    pram_version_bytes = read_sx1261_register(
        spi, SX1261_PRAM_VERSION_ADDRESS, SX1261_PRAM_VERSION_LENGTH
    )
    pram_version = pram_version_bytes.split(b"\x00", 1)[0]
    printable_count = sum(32 <= value < 127 for value in pram_version_bytes)
    has_pram_version = printable_count >= 4
    status_is_valid = mode in SX1261_MODES and command_status in SX1261_COMMAND_STATUSES

    details = {
        "status": f"0x{status:02x}",
        "status_mode": SX1261_MODES.get(mode, f"unknown (0x{mode:02x})"),
        "status_command": SX1261_COMMAND_STATUSES.get(
            command_status, f"unknown (0x{command_status:02x})"
        ),
        "pram_version": pram_version.decode("ascii", errors="replace"),
        "pram_version_hex": pram_version_bytes.hex(" "),
        "evidence": [],
    }
    if status_is_valid:
        details["evidence"].append("valid GetStatus response")
    if has_pram_version:
        details["evidence"].append("printable PRAM version")

    return ProbeResult(
        chip="SX1261",
        device=device,
        ok=status_is_valid or has_pram_version,
        details=details,
    )


def read_sx1302_register(spi: SpiDevice, address: int) -> int:
    # SX1302 SPI frames are: mux target, R/W + address[14:8], address[7:0],
    # dummy, data.  The read bit is zero.
    frame = [
        SX1302_READ_TARGET,
        (address >> 8) & 0x7F,
        address & 0xFF,
        0x00,
        0x00,
    ]
    return transfer(spi, frame)[-1]


def probe_sx1302(spi: SpiDevice, device: str) -> ProbeResult:
    version = read_sx1302_register(spi, SX1302_COMMON_VERSION_REGISTER)
    page = read_sx1302_register(spi, SX1302_COMMON_PAGE_REGISTER)
    version_is_valid = (version & 0xF0) == 0x10

    return ProbeResult(
        chip="SX1302",
        device=device,
        ok=version_is_valid,
        details={
            "version": f"0x{version:02x}",
            "version_string": f"v{version >> 4}.{version & 0x0F}",
            "expected_version": "0x10",
            "page": f"0x{page:02x}",
        },
    )


def run_spi_probe(chip: str, device: str, speed_hz: int) -> ProbeResult:
    spi: SpiDevice | None = None
    try:
        spi = open_spi(device, speed_hz)
        if chip == "SX1261":
            return probe_sx1261(spi, device)
        return probe_sx1302(spi, device)
    except Exception as exc:
        return ProbeResult(chip=chip, device=device, ok=False, error=str(exc))
    finally:
        if spi is not None:
            spi.close()


def read_i2c_register(bus: I2CBus, address: int, register: int) -> int:
    """Read one STTS751 register using an SMBus byte-data transaction."""
    return int(bus.read_byte_data(address, register)) & 0xFF


def _decode_temperature(high_byte: int, low_byte: int) -> tuple[int, float]:
    raw = (high_byte << 8) | low_byte
    if raw & 0x8000:
        raw -= 0x10000
    return raw, raw / 256.0


def probe_stts751_address(bus: I2CBus, device: str, address: int) -> ProbeResult:
    """Probe one candidate address and return structured evidence."""
    details: dict[str, Any] = {
        "address_hex": f"0x{address:02x}",
        "evidence": [],
        "warnings": [],
    }

    try:
        product_id = read_i2c_register(bus, address, STTS751_REG_PROD_ID)
        manufacturer_id = read_i2c_register(bus, address, STTS751_REG_MAN_ID)
    except Exception as exc:
        return ProbeResult(
            chip="STTS751",
            device=device,
            ok=False,
            details=details,
            error=str(exc),
            address=address,
        )

    product_name = STTS751_PRODUCT_IDS.get(product_id)
    identity_ok = (
        product_name is not None and manufacturer_id == STTS751_MANUFACTURER_ID
    )
    details.update(
        {
            "product_id": f"0x{product_id:02x}",
            "product": product_name or "unknown",
            "manufacturer_id": f"0x{manufacturer_id:02x}",
        }
    )

    try:
        revision_id = read_i2c_register(bus, address, STTS751_REG_REV_ID)
        details["revision_id"] = f"0x{revision_id:02x}"
    except Exception as exc:
        details["warnings"].append(f"revision read failed: {exc}")

    expected_product = STTS751_EXPECTED_PRODUCT_BY_ADDRESS.get(address)
    if expected_product is not None and product_id != expected_product:
        details["address_variant_match"] = False
        details["warnings"].append(
            f"address normally maps to {STTS751_PRODUCT_IDS[expected_product]}"
        )
    else:
        details["address_variant_match"] = True

    if product_name is not None:
        details["evidence"].append(f"valid product ID ({product_name})")
    if manufacturer_id == STTS751_MANUFACTURER_ID:
        details["evidence"].append("valid ST manufacturer ID")

    if identity_ok:
        try:
            status = read_i2c_register(bus, address, STTS751_REG_STATUS)
            details["status"] = f"0x{status:02x}"
            details["status_trip"] = bool(status & STTS751_STATUS_TRIPT)
            details["status_low_limit"] = bool(status & STTS751_STATUS_TRIPL)
            details["status_high_limit"] = bool(status & STTS751_STATUS_TRIPH)
        except Exception as exc:
            details["warnings"].append(f"status read failed: {exc}")

        try:
            # Match the Semtech HAL order: read LSB before MSB.
            low_byte = read_i2c_register(bus, address, STTS751_REG_TEMP_L)
            high_byte = read_i2c_register(bus, address, STTS751_REG_TEMP_H)
            raw_temperature, temperature_c = _decode_temperature(high_byte, low_byte)
            details.update(
                {
                    "temperature_raw": f"0x{raw_temperature & 0xFFFF:04x}",
                    "temperature_high": f"0x{high_byte:02x}",
                    "temperature_low": f"0x{low_byte:02x}",
                    "temperature_c": temperature_c,
                }
            )
            details["evidence"].append("temperature registers readable")
        except Exception as exc:
            details["warnings"].append(f"temperature read failed: {exc}")

    return ProbeResult(
        chip="STTS751",
        device=device,
        ok=identity_ok,
        details=details,
        address=address,
    )


def probe_stts751(device: str, addresses: Sequence[int]) -> ProbeResult:
    """Scan the candidate addresses like the sx1302_hal and summarize the result."""
    try:
        bus = open_i2c(device)
    except Exception as exc:
        return ProbeResult(chip="STTS751", device=device, ok=False, error=str(exc))

    try:
        attempts = [probe_stts751_address(bus, device, address) for address in addresses]
    finally:
        bus.close()

    probed = ", ".join(f"0x{address:02x}" for address in addresses)
    found = next((attempt for attempt in attempts if attempt.ok), None)
    if found is None:
        return ProbeResult(
            chip="STTS751",
            device=device,
            ok=False,
            details={
                "probed_addresses": probed,
                "warnings": [
                    f"0x{attempt.address:02x}: {attempt.error or 'not an STTS751'}"
                    for attempt in attempts
                ],
            },
            error="no temperature sensor found (the sx1302_hal requires one)",
        )

    details = dict(found.details)
    details["probed_addresses"] = probed
    for attempt in attempts:
        if attempt is found:
            continue
        details.setdefault("warnings", []).append(
            f"0x{attempt.address:02x}: {attempt.error or 'not an STTS751'}"
        )
    return ProbeResult(
        chip="STTS751",
        device=device,
        ok=True,
        details=details,
        address=found.address,
    )


def print_human(results: Sequence[ProbeResult]) -> None:
    for result in results:
        state = "PASS" if result.ok else "FAIL"
        location = f"{result.chip} @ {result.device}"
        if result.address is not None:
            location += f" address 0x{result.address:02x}"
        print(f"{location}: {state}")
        if result.error:
            print(f"  error: {result.error}")
        for key, value in result.details.items():
            if key in {"evidence", "warnings"}:
                if value:
                    print(f"  {key}: {', '.join(str(item) for item in value)}")
                continue
            if key == "temperature_c":
                print(f"  temperature: {value:.2f} °C")
                continue
            print(f"  {key.replace('_', ' ')}: {value}")
    print(
        "Result: PASS (SX1302 + SX1261 + STTS751 all responded)"
        if all(result.ok for result in results)
        else "Result: FAIL (one or more expected devices did not respond)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sx1261-device",
        default=DEFAULT_SX1261_DEVICE,
        metavar="PATH",
        help=f"SX1261 spidev node (default: {DEFAULT_SX1261_DEVICE})",
    )
    parser.add_argument(
        "--sx1302-device",
        default=DEFAULT_SX1302_DEVICE,
        metavar="PATH",
        help=f"SX1302 spidev node (default: {DEFAULT_SX1302_DEVICE})",
    )
    parser.add_argument(
        "--speed-hz",
        default=DEFAULT_SPEED_HZ,
        type=int,
        metavar="HZ",
        help=f"SPI clock frequency (default: {DEFAULT_SPEED_HZ})",
    )
    parser.add_argument(
        "--i2c-device",
        default=DEFAULT_I2C_DEVICE,
        metavar="PATH",
        help=f"Linux I2C device node for the temperature sensor (default: {DEFAULT_I2C_DEVICE})",
    )
    parser.add_argument(
        "--temp-sensor-address",
        dest="temp_sensor_addresses",
        action="append",
        type=_parse_i2c_address,
        metavar="ADDR",
        help="probe one STTS751 address (repeatable; default: 0x39, 0x3b and 0x38)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="apply SX1302 GPIO17 (1->0) and SX1261 GPIO5 (0->1) resets before probing",
    )
    parser.add_argument(
        "--sx1302-reset-gpio",
        type=int,
        default=DEFAULT_SX1302_RESET_GPIO,
        metavar="N",
        help=f"SX1302 reset GPIO (default: {DEFAULT_SX1302_RESET_GPIO})",
    )
    parser.add_argument(
        "--sx1261-reset-gpio",
        type=int,
        default=DEFAULT_SX1261_RESET_GPIO,
        metavar="N",
        help=f"SX1261 reset GPIO (default: {DEFAULT_SX1261_RESET_GPIO})",
    )
    parser.add_argument(
        "--reset-gpiochip",
        type=int,
        default=0,
        metavar="N",
        help="gpiochip number used by the reset GPIOs when sysfs is unavailable (default: 0)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of human-readable output",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.speed_hz <= 0:
        parser.error("--speed-hz must be positive")
    if args.sx1302_reset_gpio < 0 or args.sx1261_reset_gpio < 0:
        parser.error("reset GPIO numbers must be non-negative")
    if args.reset_gpiochip < 0:
        parser.error("reset gpiochip number must be non-negative")
    temp_sensor_addresses = (
        tuple(args.temp_sensor_addresses)
        if args.temp_sensor_addresses
        else DEFAULT_TEMP_SENSOR_ADDRESSES
    )
    if spidev is None:
        print(
            "error: Python module 'spidev' is required; install the host "
            "package (for example, python3-spidev)",
            file=sys.stderr,
        )
        return 2
    if smbus2 is None and smbus is None:
        print(
            "error: Python module 'smbus2' or 'smbus' is required; install "
            "smbus2 (or the host package python3-smbus)",
            file=sys.stderr,
        )
        return 2

    reset_holds: list[_GpiosetHold] = []
    try:
        if args.reset:
            try:
                reset_holds = reset_bundled_radios(
                    args.sx1302_reset_gpio, args.sx1261_reset_gpio, args.reset_gpiochip
                )
            except OSError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2

        results = [
            run_spi_probe("SX1261", args.sx1261_device, args.speed_hz),
            run_spi_probe("SX1302", args.sx1302_device, args.speed_hz),
            probe_stts751(args.i2c_device, temp_sensor_addresses),
        ]
    finally:
        for hold in reset_holds:
            hold.close()
    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        print_human(results)
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
