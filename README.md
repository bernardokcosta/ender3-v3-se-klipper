# Klipper for Ender 3 V3 SE

[![Ender 3 V3 SE CI](https://github.com/bernardokcosta/ender3-v3-se-klipper/actions/workflows/ender3-v3-se.yml/badge.svg)](https://github.com/bernardokcosta/ender3-v3-se-klipper/actions/workflows/ender3-v3-se.yml)
[![Latest release](https://img.shields.io/github/v/release/bernardokcosta/ender3-v3-se-klipper?include_prereleases)](https://github.com/bernardokcosta/ender3-v3-se-klipper/releases/latest)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](COPYING)

This is an unofficial [Klipper](https://www.klipper3d.org/) fork for a
physically verified Creality Ender 3 V3 SE with:

- Mainboard `CR4NS200320C14`
- `GD32F303RET6` microcontroller
- Stock TJC3224/DWIN 3.2 inch display
- USB serial host connection and stock display cabling

The implementation is based on current upstream Klipper and keeps the machine
support isolated in a small MCU bridge, a Klippy extra, modular configuration,
tests, and documentation. It does not include the historical PRTouch/HX711
auto-Z implementation; probing uses Klipper's upstream CR-Touch support.

## Safety and hardware scope

This firmware is an early public release and has not completed physical
acceptance testing on every board or display revision. A `C14` board marking
does not identify the MCU by itself. Open the electronics enclosure and verify
the actual chip before flashing.

Do not use this build on an STM32F401 board. Before printing, verify heater
outputs and their matching thermistors, endstops, motor directions, travel
limits, CR-Touch, Z offset, and emergency power-off behavior. The software and
its contributors provide no warranty; see the GPLv3 warranty terms in
[`COPYING`](COPYING).

## Downloads

Download the newest CI-validated build from
[GitHub Releases](https://github.com/bernardokcosta/ender3-v3-se-klipper/releases/latest).
Each release provides:

- `klipper.bin`: MCU firmware for the verified GD32F303 target
- `klipper.dict`: matching MCU protocol dictionary
- `defconfig`: normalized Kconfig used for the build
- `BUILD-METADATA.txt`: source commit, upstream base, target, and build date
- `SHA256SUMS`: checksums for the downloadable build files

The Klippy host checkout and MCU firmware must come from the same release tag.
Do not combine the release binary with a different branch or commit.

To flash, place `klipper.bin` on a FAT32 microSD card and give it a short,
previously unused filename ending in `.bin`, for example `kli0917.bin`. Read the
complete [installation and commissioning guide](docs/Ender3_V3_SE.md) before
powering or moving the printer.

## Configuration

The entry configuration is:

[`config/printer-creality-ender3-v3-se-gd32f303-2023.cfg`](config/printer-creality-ender3-v3-se-gd32f303-2023.cfg)

Copy it and the modular directory to the Mainsail configuration directory:

```sh
cp config/printer-creality-ender3-v3-se-gd32f303-2023.cfg \
  ~/printer_data/config/printer.cfg
cp -r config/ender3-v3-se ~/printer_data/config/
```

Confirm the MCU path in `config/ender3-v3-se/board.cfg`. PID values, Z offset,
bed mesh, pressure advance, and input shaping are intentionally not shipped as
machine-independent values. Calibrate them on the physical printer.

## Documentation

- [Installation, build, flashing, and commissioning](docs/Ender3_V3_SE.md)
- [Stock display architecture and diagnostics](docs/E3V3SE_Display.md)
- [Fork comparison, issue audit, and port decisions](docs/E3V3SE_Port_Analysis.md)
- [Official Klipper documentation](https://www.klipper3d.org/)

## Credits and provenance

This repository preserves Klipper's Git history and is distributed under the
same GNU GPLv3 license. The Ender 3 V3 SE work was made possible by:

- [Klipper](https://github.com/Klipper3d/klipper), created by Kevin O'Connor and
  maintained by the Klipper contributors, for the firmware, host architecture,
  build system, documentation, and GPLv3 codebase.
- [Joao Pedro Curti](https://github.com/jpcurti) and contributors to
  [ender3-v3-se-klipper-with-display](https://github.com/jpcurti/ender3-v3-se-klipper-with-display)
  for the original Ender 3 V3 SE display integration, behavior, protocol
  research, and issue history used as the basis for this rewritten port.
- [odwdinc/DWIN_T5UIC1_LCD](https://github.com/odwdinc/DWIN_T5UIC1_LCD) for the
  earlier DWIN T5L display work on which the TJC3224 implementation was based.
- [E4ST2W3ST](https://github.com/E4ST2W3ST) for the
  [Klipper serial bridge proposal](https://github.com/Klipper3d/klipper/commit/6469418d73be6743a7130b50fdb5a57d311435ca).
- [Clark Scheff / 0xD34D](https://github.com/0xD34D) for the
  [machine fork](https://github.com/0xD34D/klipper_ender3_v3_se) and
  [configuration reference](https://github.com/0xD34D/ender3-v3-se-klipper-config)
  used to study the hardware, community configuration, and historical PRTouch
  implementation.
- [huhen/Ender3_V3_SE_for_klipper](https://github.com/huhen/Ender3_V3_SE_for_klipper),
  distributed under GPLv3, for the documented CR4NS200320C13 pinout and
  configuration used as the licensed electrical pin-map basis for this port.
- [bootuz-dinamon](https://github.com/bootuz-dinamon/ender3-v3-se-full-klipper)
  and [FinalX1992](https://github.com/FinalX1992) for additional community
  hardware findings used to cross-check TMC UART and screw configuration.
- Everyone who reported, diagnosed, and fixed issues in those projects.

The detailed revision and commit provenance is recorded in
[`docs/E3V3SE_Port_Analysis.md`](docs/E3V3SE_Port_Analysis.md).

## License

Klipper and the software and configuration distributed by this modified fork
are Free Software under the [GNU General Public License, version 3](COPYING).
Source files retain their applicable copyright and license notices. The
complete corresponding source for each binary release is the repository state
at that release tag.

No Creality display firmware, image pack, or other proprietary binary asset is
redistributed here. Creality, Ender, DWIN, and TJC names may be trademarks of
their respective owners. This project is not affiliated with or endorsed by
Creality, DWIN, TJC, or the upstream Klipper project.
