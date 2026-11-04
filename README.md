# De’Longhi Daedalus for Home Assistant

[![HACS Custom Repository](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square)](https://github.com/hacs/default)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)

> **Status: early, in active development.** Sign-in, power control over your home network (including without internet), finding the machine automatically, and machine status and alarms work on an Eletta Ultra (ECAM 470.85). Brewing from Home Assistant is not tested yet. Built on [sk7n4k3d’s original work](#credits).

A Home Assistant integration for modern De’Longhi smart bean-to-cup espresso machines built on the next-generation **Daedalus** architecture (such as the **Eletta Ultra ECAM 470.85** and appliances running the official *My Coffee Lounge* application).

Built with low-latency local control, instant state synchronization, and zero cloud lock-in.

---

## Highlights

* **Instantaneous Local Control (LAN2LAN)** — Direct, low-latency secure WebSocket communication (`/ws/lan2lan`) directly with your coffee machine on your local Wi-Fi network.
* **Dual-Plane Architecture** — Automatic cloud discovery and pairing via SAP Gigya SSO and AWS IoT with seamless fallback to local network execution.
* **Standby & Power Toggle** — Reliable remote wake and standby power switching.
* **Extraction Telemetry** — Real-time dispensing feedback tracking coffee volume, aroma strength, pre-infusion stages, and grind dosage.
* **Quick Brew Triggers** — Single-tap action buttons for your favorite recipes (Espresso, Coffee XL, Americano, Doppio+, Long, and Hot Water).
* **Settings & Diagnostics** — Read extraction temperature, auto-off timers, and connectivity diagnostics.

---

## Supported Appliances

* **De’Longhi Eletta Ultra** (ECAM 470.85 series / ECAM47080)
* Other modern De’Longhi appliances utilizing the **Daedalus** ecosystem and the official *My Coffee Lounge* app.

*(Older De’Longhi machines that use the Coffee Link app are supported by [sk7n4k3d/delonghi-ha](https://github.com/sk7n4k3d/delonghi-ha).)*

---

## Installation

### Method 1: HACS (Recommended)

1. Ensure [HACS (Home Assistant Community Store)](https://hacs.xyz/) is installed.
2. In Home Assistant, open **HACS** → **Integrations**.
3. Click the three dots in the upper-right corner and select **Custom repositories**.
4. Enter the repository URL:
   ```text
   https://github.com/bek00letz/delonghi-daedalus-ha
   ```
5. Choose **Integration** as the category and click **Add**.
6. Search for **De’Longhi Daedalus** and click **Download**.
7. Restart Home Assistant.

### Method 2: Manual Installation

1. Download the latest release from the [Releases](https://github.com/bek00letz/delonghi-daedalus-ha/releases) page.
2. Copy the `custom_components/delonghi_daedalus` directory into your Home Assistant `<config>/custom_components/` folder.
3. Restart Home Assistant.

---

## Configuration

1. In Home Assistant, navigate to **Settings** → **Devices & Services** → **Add Integration**.
2. Search for **De’Longhi Daedalus**.
3. Enter your De’Longhi account credentials (*email* and *password* used in the My Coffee Lounge application).
4. Leave **Coffee machine’s IP address** empty: Home Assistant finds the machine on your network (it takes a few seconds). If it can’t, it asks for the address, which your router’s device list shows.
5. Click **Submit**. If several machines are linked to your account, choose one.

If your router later gives the machine a new address, Home Assistant finds it again on its own.

---

## Entities & Capabilities

| Platform | Entity | Description |
| :--- | :--- | :--- |
| **Switch** | Power | Wakes the machine or puts it into standby. Follows the machine’s own reports. |
| **Sensor** | Status | The machine’s own status, such as *Ready* or *Standby*. Raw report details are attributes. |
| **Sensor** | Last drink | Last drink brewed, with extraction details as attributes |
| **Sensor** | Coffee temperature | Configured brewing temperature |
| **Sensor** | Auto-off timer | Time before the machine goes into standby on its own |
| **Binary sensor** | Water tank empty, Water tank missing, Grounds container full, Out of coffee beans, Descaling needed, Water filter, Brew unit cleaning, Cleaning needed, General fault | Alarms the machine reports. On means it needs attention. |
| **Binary sensor** | 12 technical faults (grinder, heater sensor, fan, …) | Created but switched off. Turn them on under the device’s entities if you want them. |
| **Binary sensor** | Home network connection, Cloud connection | Whether Home Assistant is connected to the machine directly and through De’Longhi’s cloud |
| **Button** | Brew coffee XL, espresso, coffee, long coffee, doppio+, americano; Hot water | One-tap drinks (not yet tested) |

The machine reports its status when something changes, for example when it wakes, warms up or brews. After Home Assistant restarts, status and alarms show *Unknown* until the next report.

---

## Without internet

Control over your home network keeps working when the internet or De’Longhi’s cloud is down:

* **Power, brewing and settings keep working** for as long as the saved sign-in token is valid. Each token lasts 90 days, and Home Assistant renews it on its own whenever the internet is available and fewer than 30 days remain.
* **The tradeoff is status after a restart.** The machine only reports its status when something changes, so after Home Assistant restarts, status and alarms stay *Unknown* until the machine next reports, for example when you wake it or brew. Reading the last status from De’Longhi’s cloud at startup is planned. That needs the internet, so offline, the *Unknown* gap stays.
* **Setting up needs the internet once**, to sign in to your De’Longhi account. So does renewing the token: if Home Assistant can’t reach the cloud for the whole 90 days, local control stops until it can.

---

## Privacy & Security

* Commands go straight from Home Assistant to the coffee machine over your home network, encrypted with TLS.
* The machine accepts your De’Longhi account’s sign-in token as its key, so Home Assistant signs in to De’Longhi’s cloud to get one. The token lasts 90 days and is renewed automatically.
* Home Assistant records your machine’s certificate the first time the machine accepts your token, and from then on only sends the token to a device presenting that exact certificate. If something else answers at the machine’s address, Home Assistant refuses it and shows a repair notice. After resetting or re-pairing the machine, turn on **Trust the coffee machine again** in the integration’s options.
* When searching your network, Home Assistant only offers the token to devices with a coffee-machine certificate, and once your machine is known, it searches by certificate alone without sending the token anywhere.
* Like every Home Assistant integration, your email, password and token are stored in Home Assistant’s configuration folder, which is not encrypted. Diagnostics downloads leave them out.
* No analytics or tracking. Nothing is sent anywhere except De’Longhi’s own services and your machine.

---

## Credits

This integration started as a fork of [sk7n4k3d/delonghi-daedalus-ha](https://github.com/sk7n4k3d/delonghi-daedalus-ha). [sk7n4k3d](https://github.com/sk7n4k3d)’s original work mapped the Daedalus stack: the De’Longhi sign-in, the local `/ws/lan2lan` handshake that uses the account token, the setup flow and diagnostics, and the HACS packaging and checks this repository still uses. sk7n4k3d also maintains [delonghi-ha](https://github.com/sk7n4k3d/delonghi-ha) for older Coffee Link machines.

---

## License

Released under the [MIT License](LICENSE).

