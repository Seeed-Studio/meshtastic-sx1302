#!/usr/bin/env python3
"""Probe the SX1261 and SX1302 connected to SPI0.

The default wiring is:

    SX1302 -> /dev/spidev0.0 (CS0)
    SX1261 -> /dev/spidev0.1 (CS1)

By default the probe only performs reads.  With ``--reset`` it applies the
board reset wiring before probing:

    SX1302 GPIO17: 1 -> 0
    SX1261 GPIO5:  0 -> 1

Requires the Linux ``spidev`` Python module.  Run it on the target host with:

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


DEFAULT_SX1261_DEVICE = "/dev/spidev0.1"
DEFAULT_SX1302_DEVICE = "/dev/spidev0.0"
DEFAULT_SPEED_HZ = 2_000_000
DEFAULT_SX1302_RESET_GPIO = 17
DEFAULT_SX1261_RESET_GPIO = 5

SX1261_GET_STATUS = 0xC0
SX1261_READ_REGISTER = 0x1D
SX1261_PRAM_VERSION_ADDRESS = 0x0320
SX1261_PRAM_VERSION_LENGTH = 15

SX1302_READ_TARGET = 0x00
SX1302_COMMON_VERSION_REGISTER = 0x5606
SX1302_COMMON_PAGE_REGISTER = 0x5600

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


@dataclass
class ProbeResult:
    chip: str
    device: str
    ok: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


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


def run_probe(chip: str, device: str, speed_hz: int) -> ProbeResult:
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


def print_human(results: Sequence[ProbeResult]) -> None:
    for result in results:
        state = "PASS" if result.ok else "FAIL"
        print(f"{result.chip} @ {result.device}: {state}")
        if result.error:
            print(f"  error: {result.error}")
            continue
        for key, value in result.details.items():
            if key == "evidence":
                if value:
                    print(f"  {key}: {', '.join(value)}")
                continue
            print(f"  {key.replace('_', ' ')}: {value}")
    print(
        "Result: PASS (both expected chips responded)"
        if all(result.ok for result in results)
        else "Result: FAIL (one or more expected chips did not respond)"
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
    if spidev is None:
        print(
            "error: Python module 'spidev' is required; install the host "
            "package (for example, python3-spidev)",
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
            run_probe("SX1261", args.sx1261_device, args.speed_hz),
            run_probe("SX1302", args.sx1302_device, args.speed_hz),
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
