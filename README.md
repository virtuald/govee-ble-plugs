# Govee BLE Smart Plug Integration for HomeAssistant

![Govee Logo](assets/govee-logo.png)

Control your Govee Smart Plugs via BLE directly from HomeAssistant!

## Features

- 🚀 **Direct BLE Control**: No need for middlewares or bridges. Connect and control your Govee devices directly through Bluetooth Low Energy.

Supported devices:

* H5080 Smart Plug
* H5082 Dual Smart Plug
* H5086 Smart Plug with Energy Monitoring (no Energy Monitoring as of 2025/08/23)

## Configuration

### What is needed

For Direct BLE Control:
- Before you begin, make certain HomeAssistant can access BLE on your platform. Ensure your HomeAssistant instance is granted permissions to utilize the Bluetooth Low Energy of your host machine.

## Usage

With the integration setup, your Govee devices will appear as entities within HomeAssistant. All you need to do is select your device model when adding it.

## Installation

* The installation is done inside [HACS](https://hacs.xyz/) (Home Assistant Community Store). If you don't have HACS, you must install it before adding this integration. [Installation instructions here.](https://hacs.xyz/docs/setup/download)
* At the present moment this integration has not been published to HACS, but you can still install it as a custom integration. See https://www.hacs.xyz/docs/faq/custom_repositories/ for details

### H5083 pairing

The H5083 encrypts its Bluetooth traffic. When the pairing screen appears, Home Assistant connects
to the plug and its light starts flashing; press the plug's button, then click Submit. Each pairing
issues a new auth key, so pairing in Home Assistant stops the Govee app from controlling the plug
over Bluetooth (and re-pairing in the app stops Home Assistant).

## Troubleshooting for BLE

If you're facing issues with the integration, consider the following steps:

1. **Check BLE Connection**: 
   
   Ensure that the Govee device is within the Bluetooth range of your HomeAssistant host machine.

2. **Model Check**:

   Check that you are using a supported device

3. **Logs**:

   HomeAssistant logs can provide insights into any issues. Navigate to `Configuration > Logs` to review any error messages related to the Govee integration.

---

## Support & Contribution

- **Found an Issue?**

   Raise it in the [Issues section](https://github.com/virtuald/govee_ble_plugs/issues) of this repository.

- **Device support**:

   I do not plan to add support for devices that I do not own, but I'm happy to accept support for new devices.

- **Contributions**:

   I am happy to accept contributions to improve this integration or add new local-only devices. I will not accept cloud-based integrations.


Credit
------

H5080 and H5086 support would not have been possible without the scripts from https://github.com/egold555/Govee-Reverse-Engineering (and if you want to add support for more devices then this is a good place to start!).

This integration was inspired by https://github.com/Beshelmek/govee_ble_lights/ and I started writing this based on its code, but I eventually just copied a bunch of code from the [keymitt_ble integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/keymitt_ble) since it was much closer to what actually needed to be accomplished.

This integration is available under the Apache 2.0 license.
