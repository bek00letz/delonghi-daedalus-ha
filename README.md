# De’Longhi Daedalus for Home Assistant

[![HACS Custom Repository](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square)](https://github.com/hacs/default)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)

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

*(Note: Older De’Longhi appliances utilizing the legacy Ayla Networks cloud and Coffee Link app are supported by `sk7n4k3d/delonghi-ha`).*

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
4. *(Optional but Recommended)* Enter your coffee machine’s **Local IP address** (e.g., `192.168.1.50`). Providing the local IP enables direct, instantaneous LAN2LAN communication with zero cloud latency.
5. Click **Submit**. If multiple appliances are associated with your account, select the desired machine from the list.

---

## Entities & Capabilities

| Platform | Entity Name | Description |
| :--- | :--- | :--- |
| **Switch** | Power | Wake appliance from standby or put into sleep mode |
| **Sensor** | Status | Current machine state (*Ready*, *Standby*, *Brewing*, *Offline*) |
| **Sensor** | Last Beverage | Last recipe dispensed along with detailed extraction attributes |
| **Sensor** | Coffee Temperature | Configured brew extraction temperature level |
| **Sensor** | Auto-Off Timer | Configured duration before entering automatic standby |
| **Binary Sensor** | Local Network | Indicates active local LAN2LAN WebSocket connectivity |
| **Binary Sensor** | Cloud Connection | Indicates appliance status reported via De’Longhi cloud |
| **Button** | Brew Coffee XL | One-touch brewing trigger for Coffee XL (Recipe 33) |
| **Button** | Brew Espresso | One-touch brewing trigger for Espresso |
| **Button** | Brew Coffee | One-touch brewing trigger for standard Coffee |
| **Button** | Brew Long | One-touch brewing trigger for Long Coffee |
| **Button** | Brew Doppio+ | One-touch brewing trigger for Doppio+ |
| **Button** | Brew Americano | One-touch brewing trigger for Americano |
| **Button** | Dispense Hot Water | One-touch dispensing trigger for Hot Water |

---

## Privacy & Security

This integration respects user privacy:
* All local network commands travel directly between Home Assistant and the coffee machine on your private subnet over TLS.
* Account credentials are used solely to negotiate initial session tokens and are stored securely within Home Assistant’s encrypted entry store.
* No telemetry, metrics, or personal data are ever transmitted to third-party endpoints.

---

## License

Released under the [MIT License](LICENSE).
