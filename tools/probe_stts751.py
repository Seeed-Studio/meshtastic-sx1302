#!/usr/bin/env python3
"""Probe an STTS751 temperature sensor on Linux I2C bus 1.

The supported STTS751 variants used by the Semtech concentrator designs are:

    STTS751-0 -> 0x39
    STTS751-1 -> 0x3B

The probe is read-only.  It validates the product and manufacturer ID, then
reads the status and temperature when the device identifies as an STTS751.
Run it on the target host with::

    tools/probe_stts751.py

Use ``--help`` to override the I2C device path or addresses.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, Sequence

try:  # smbus2 is pure Python and is commonly used on Raspberry Pi hosts.
    import smbus2
except ImportError:  # pragma: no cover - depends on the target host
    smbus2 = None  # type: ignore[assignment]

try:
    import smbus
except ImportError:  # pragma: no cover - depends on the target host
    smbus = None  # type: ignore[assignment]


DEFAULT_I2C_DEVICE = "/dev/i2c-1"
DEFAULT_ADDRESSES = (0x39, 0x3B)

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
    0x39: 0x00,
    0x3B: 0x01,
}
STTS751_MANUFACTURER_ID = 0x53


class I2CBus(Protocol):
    def read_byte_data(self, address: int, register: int) -> int:
        ...

    def close(self) -> None:
        ...


@dataclass
class ProbeResult:
    chip: str
    device: str
    address: int
    ok: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _parse_i2c_path(path: str) -> int:
    """Return the Linux I2C bus number encoded in a device path."""
    match = re.fullmatch(r"(?:/dev/)?i2c-(\d+)", path)
    if not match:
        raise ValueError(
            f"{path!r} is not a Linux I2C device path "
            "(expected /dev/i2c-N)"
        )
    return int(match.group(1))


def _parse_address(value: str) -> int:
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


def read_register(bus: I2CBus, address: int, register: int) -> int:
    """Read one STTS751 register using an SMBus byte-data transaction."""
    return int(bus.read_byte_data(address, register)) & 0xFF


def _decode_temperature(high_byte: int, low_byte: int) -> tuple[int, float]:
    raw = (high_byte << 8) | low_byte
    if raw & 0x8000:
        raw -= 0x10000
    return raw, raw / 256.0


def probe_address(bus: I2CBus, device: str, address: int) -> ProbeResult:
    """Probe one candidate address and return structured evidence."""
    details: dict[str, Any] = {
        "address_hex": f"0x{address:02x}",
        "evidence": [],
        "warnings": [],
    }

    try:
        product_id = read_register(bus, address, STTS751_REG_PROD_ID)
        manufacturer_id = read_register(bus, address, STTS751_REG_MAN_ID)
    except Exception as exc:
        return ProbeResult(
            chip="STTS751",
            device=device,
            address=address,
            ok=False,
            details=details,
            error=str(exc),
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
        revision_id = read_register(bus, address, STTS751_REG_REV_ID)
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
            status = read_register(bus, address, STTS751_REG_STATUS)
            details["status"] = f"0x{status:02x}"
            details["status_trip"] = bool(status & STTS751_STATUS_TRIPT)
            details["status_low_limit"] = bool(status & STTS751_STATUS_TRIPL)
            details["status_high_limit"] = bool(status & STTS751_STATUS_TRIPH)
        except Exception as exc:
            details["warnings"].append(f"status read failed: {exc}")

        try:
            # Match the Semtech HAL order: read LSB before MSB.
            low_byte = read_register(bus, address, STTS751_REG_TEMP_L)
            high_byte = read_register(bus, address, STTS751_REG_TEMP_H)
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
        address=address,
        ok=identity_ok,
        details=details,
    )


def probe_bus(bus: I2CBus, device: str, addresses: Sequence[int]) -> list[ProbeResult]:
    """Probe all requested addresses while keeping one bus handle open."""
    return [probe_address(bus, device, address) for address in addresses]


def print_human(results: Sequence[ProbeResult]) -> None:
    for result in results:
        state = "PASS" if result.ok else "FAIL"
        print(
            f"{result.chip} @ {result.device} "
            f"address 0x{result.address:02x}: {state}"
        )
        if result.error:
            print(f"  error: {result.error}")
            continue
        for key, value in result.details.items():
            if key in {"evidence", "warnings"}:
                if value:
                    print(f"  {key}: {', '.join(str(item) for item in value)}")
                continue
            if key == "temperature_c":
                print(f"  temperature: {value:.2f} °C")
                continue
            print(f"  {key.replace('_', ' ')}: {value}")

    found = [result for result in results if result.ok]
    if found:
        addresses = ", ".join(f"0x{result.address:02x}" for result in found)
        print(f"Result: PASS (STTS751 found at {addresses})")
    else:
        print("Result: FAIL (no valid STTS751 found at the requested addresses)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device",
        "--i2c-device",
        dest="device",
        default=DEFAULT_I2C_DEVICE,
        metavar="PATH",
        help=f"Linux I2C device node (default: {DEFAULT_I2C_DEVICE})",
    )
    parser.add_argument(
        "--address",
        dest="addresses",
        action="append",
        type=_parse_address,
        metavar="ADDR",
        help="probe one address (repeatable; default: 0x39 and 0x3b)",
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
    addresses = tuple(args.addresses) if args.addresses else DEFAULT_ADDRESSES

    if smbus2 is None and smbus is None:
        print(
            "error: Python module 'smbus2' or 'smbus' is required; install "
            "smbus2 (or the host package python3-smbus)",
            file=sys.stderr,
        )
        return 2

    try:
        bus = open_i2c(args.device)
    except Exception as exc:
        print(f"error: failed to open I2C bus {args.device}: {exc}", file=sys.stderr)
        return 2

    try:
        results = probe_bus(bus, args.device, addresses)
    finally:
        bus.close()

    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        print_human(results)
    return 0 if any(result.ok for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
