# Installing Home Assistant for GrowCube on Windows with VirtualBox

This guide explains how to install Home Assistant on Windows using a VirtualBox virtual machine. GrowCube integration installation is done later through HACS, so this document does not use Samba or manual `custom_components` copying.

There is also a silent companion video that shows the installation flow visually.

## Requirements

- A Windows computer.
- VirtualBox installed.
- The Home Assistant image for VirtualBox.
- A stable local network where Home Assistant and GrowCube can be connected.

## 1. Install VirtualBox

1. Open the VirtualBox downloads page:
   [https://www.virtualbox.org/wiki/Downloads](https://www.virtualbox.org/wiki/Downloads)
2. Download the VirtualBox installer for Windows.
3. Run the installer and complete the installation.

## 2. Download Home Assistant for Windows / VirtualBox

1. Open the Home Assistant Windows installation guide:
   [https://www.home-assistant.io/installation/windows/](https://www.home-assistant.io/installation/windows/)
2. Download the Home Assistant image for VirtualBox.
3. Extract the downloaded archive if it was downloaded as an archive.

After extracting it, you should have a Home Assistant virtual disk file. You will need it when creating the virtual machine.

## 3. Create The Home Assistant Virtual Machine

1. Open VirtualBox.
2. In the top menu, select `Machine` -> `New`.
3. In the `Name` field, enter:

   ```text
   HomeAssistant
   ```

4. In the `Type` field, select:

   ```text
   Linux
   ```

5. In the `Version` field, select:

   ```text
   Linux 2.6 / 3.x / 4.x / 5.x (64-bit)
   ```

6. Assign resources to the virtual machine:

   ```text
   Base memory: 4096 MB
   Processors: 2
   ```

7. Make sure to enable `EFI`.

   ![VirtualBox virtual machine settings](images/1.png)

8. On the hard disk step, select:

   ```text
   Use an existing virtual hard disk file
   ```

9. Select the downloaded and extracted Home Assistant virtual disk.
10. Finish creating the virtual machine.

## 4. Configure The Virtual Machine Network

After creating the virtual machine, change its network connection type.

1. In VirtualBox, select the `HomeAssistant` virtual machine.
2. Open `Settings`.
3. Go to `Network`.
4. For the network adapter, select:

   ```text
   Bridged Adapter
   ```

5. Save the settings.

This is required so Home Assistant is available on your local network and can communicate with GrowCube.

## 5. Start Home Assistant

1. Start the `HomeAssistant` virtual machine.
2. Wait for Home Assistant to finish booting.
3. Open a browser on your computer and try this address:

   ```text
   http://homeassistant.local:8123
   ```

   If that address does not open, try:

   ```text
   http://homeassistant:8123
   ```

4. If neither address opens, find the IPv4 address in the virtual machine console. It is usually shown next to `IPv4`.
5. Open the browser and go to:

   ```text
   http://<IPv4-address>:8123
   ```

   For example:

   ```text
   http://192.168.1.50:8123
   ```

6. Complete the initial Home Assistant onboarding.

   ![Home Assistant onboarding page](images/2.png)

## 6. Install HACS

GrowCube is installed through HACS, the Home Assistant Community Store.

1. Open the official HACS installation guide:
   [https://www.hacs.xyz/docs/use/download/download/](https://www.hacs.xyz/docs/use/download/download/)
2. Follow the installation method recommended for your Home Assistant installation.
3. Restart Home Assistant after installing HACS.
4. Open Home Assistant and complete HACS setup if Home Assistant asks for it.

## 7. Install The GrowCube Integration Through HACS

1. Open **HACS -> Integrations**.
2. Open the three-dot menu.
3. Choose **Custom repositories**.
4. Paste the repository URL:

   ```text
   https://github.com/lilbouu/GrowCube-HA
   ```

5. Select **Integration** as the category.
6. Add the repository.
7. Install **GrowCube**.
8. Restart Home Assistant.

## 8. Connect GrowCube To Wi-Fi

Before adding the GrowCube integration, GrowCube must be connected to the same local network as Home Assistant.

Important: GrowCube can accept only one TCP connection at a time. Before searching for it in Home Assistant, make sure it is not already connected to another device, such as the original app, a control panel, or another Home Assistant instance. Disconnect or turn off those clients first.

### If GrowCube Is Already Connected To Your Wi-Fi

Continue with step 9 and add GrowCube in Home Assistant.

### If GrowCube Is Not Connected To Your Wi-Fi Yet

Use the GrowCube Wi-Fi provisioning application.

In this package, the application is located here:

```text
tools/provision_growcube.exe
```

The application will:

1. ask you to connect Windows to the GrowCube setup Wi-Fi;
2. wait until Windows is connected to the GrowCube Wi-Fi;
3. scan available Wi-Fi networks;
4. let you choose your home 2.4 GHz Wi-Fi network;
5. ask for the Wi-Fi password;
6. send the Wi-Fi setup command to GrowCube;
7. show the IP address reported by GrowCube after it joins your network.

GrowCube supports 2.4 GHz Wi-Fi only. Do not choose a 5 GHz Wi-Fi network.

After provisioning succeeds, Windows may remain connected to the GrowCube setup Wi-Fi. Switch Windows back to your normal local Wi-Fi before continuing. Home Assistant and GrowCube must be on the same local network.

After Windows is back on the local Wi-Fi, add GrowCube in Home Assistant using either automatic search or the IP address shown by the provisioning application.

## 9. Add GrowCube In Home Assistant

1. In Home Assistant, open `Settings`.
2. Go to `Devices & services`.
3. Select `Add integration`.
4. Search for:

   ```text
   GrowCube
   ```

5. Add GrowCube.
6. If Home Assistant asks for the GrowCube IP address, enter the device IP address from your local network.
7. If you do not know the IP address, use automatic search and enter your local subnet, for example `192.168.0.0/24`.

After adding the integration, Home Assistant will create the GrowCube entities.

## 10. Create A GrowCube Dashboard

1. In Home Assistant, go to `Dashboards`.
2. Create a new dashboard.
3. Name it:

   ```text
   GrowCube
   ```

4. Open the new dashboard.
5. Open the YAML text editor.
6. Paste the YAML from:

   ```text
   docs/lovelace-growcube-dashboard.yaml
   ```

The current recommended YAML uses one main card:

```yaml
title: GrowCube
views:
  - title: GrowCube
    path: growcube
    panel: true
    cards:
      - type: custom:growcube-card
        overview: dashboard
```

The full dashboard also includes separate pages for `Plant A`, `Plant B`, `Plant C`, and `Plant D`.

## 11. Verify That Everything Works

After installation, check the following:

1. The dashboard shows the `Plants`, `Tank`, `Status`, and `Recent activity` sections.
2. The `Plants` section shows GrowCube plants and channels.
3. The `Tank` section shows the water level.
4. If multiple GrowCubes are added, the device switcher appears in the upper-right corner of the dashboard.
5. Opening a plant card shows the settings for the selected channel.

## Troubleshooting

### Home Assistant Does Not Open In The Browser

Try these addresses:

```text
http://homeassistant.local:8123
http://homeassistant:8123
http://<IPv4-address>:8123
```

Also check that the virtual machine network type is set to `Bridged Adapter`.

### HACS Does Not Show GrowCube

Check that the custom repository was added with category `Integration` and that the repository URL is correct:

```text
https://github.com/lilbouu/GrowCube-HA
```

### The GrowCube Integration Does Not Find The Device

1. Make sure Home Assistant and GrowCube are on the same local network.
2. Make sure GrowCube is not connected to another app, control panel, or Home Assistant instance.
3. Try automatic search with your local subnet, for example `192.168.0.0/24`.
4. If you know the GrowCube IP address, enter it manually.
