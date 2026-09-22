# Seeed XIAO ESP32-C6

Matter over Thread firmware for the BMS DOA `Seeed Studio XIAO ESP32-C6`
node on the `Seeed Studio Expansion Board Base for XIAO v1.1`.

## Hardware

- Target: `ESP32-C6`, `4 MB` flash
- Transport: Matter over Thread
- Commissioning: BLE
- Status LED: XIAO user LED on `GPIO 15`, active-low
- User button: expansion board user button on `D1` / `GPIO 1`, active-low
- Expansion buzzer: `A3` / `D3` / `GPIO 21`, beeps on `USER` press
- Expansion I2C: `D4` / `GPIO 22` as SDA, `D5` / `GPIO 23` as SCL, 100 kHz
- Expansion OLED: onboard `0.96"` I2C OLED, auto-probed at `0x3c` / `0x3d`
- Expansion peripherals: RTC, MicroSD, Grove connectors, battery management

## Device Model

- Endpoint `1`: `Temperature Sensor` using the ESP32-C6 internal temperature sensor
- Endpoint `2`: `Contact Sensor` using the expansion board `USER` button
- Matter actions: `Reboot`, plus `Identify` on the root device and contact sensor endpoint

## Matter Identity

- VendorID: `0xFFF1`
- VendorName: `BMS DOA`
- ProductID: `0x8004`
- ProductName: `Seeed XIAO ESP32-C6`
- SoftwareVersion: current git commit
- QR code: `MT:4CT91CEK01Q90648G00`
- Manual pairing code: `33331712336`
- Setup passcode: `20202021`
- Discriminator: `3585 (0xE01)`
- Serial: runtime `BMS-XC6-<MAC6>` from base MAC

## Intended Role

This board is a compact powered Matter over Thread node for quick sensor and
UI experiments on the XIAO expansion base. The first firmware profile keeps
the board simple:

- Thread connectivity and Matter commissioning
- internal chip temperature reporting
- expansion board user button as a contact sensor
- expansion board OLED with `HELLO WORLD`, node/status/temperature/button text
- expansion board buzzer feedback on user button press
- active-low status LED patterns

The expansion board RTC, MicroSD, and Grove connectors are mapped in
documentation but are not enabled in this first firmware profile.

## LED Status

- `pulse` - boot
- `blink` - commissioning window open / not yet commissioned
- `slow blink` - commissioned but Thread not attached
- `dim steady` - commissioned and online
- `fast blink` - `USER` held for commissioning
- `rapid blink` - factory reset preview / reset active

## USER Button

- `< 7 s` - contact sensor only
- `7-15 s` - open a `180 s` commissioning window
- `>= 15 s` - factory reset

## Pairing Persistence

Normal `flash` updates preserve NVS and do not require re-pairing. `erase-flash`,
factory reset, or NVS wipe require fresh commissioning.

## Build

Run from `seeedXiaoEsp32C6/matter-node`:

```bash
source /home/nomad375/.espressif/tools/activate_idf_v5.4.1.sh
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py build
```

## Flash

```bash
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py -p /dev/ttyACM0 flash
```

## Important Files

- `seeedXiaoEsp32C6/matter-node/main/main.cpp`
- `seeedXiaoEsp32C6/matter-node/sdkconfig.defaults`
- `seeedXiaoEsp32C6/matter-node/CMakeLists.txt`
- `seeedXiaoEsp32C6/matter-node/tools/generate_onboarding_card.py`
- `seeedXiaoEsp32C6/matter-node/matter-node-card.png`
