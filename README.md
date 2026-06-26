# GrowCube for Home Assistant

Home Assistant custom integration and Lovelace dashboard for Elecrow GrowCube.

This package includes:

- the Home Assistant integration in `custom_components/growcube`;
- a ready dashboard YAML in `docs/lovelace-growcube-dashboard.yaml`;
- a Windows Home Assistant installation guide in `INSTALL_HOME_ASSISTANT_WINDOWS_.md`;
- a Windows GrowCube Wi-Fi provisioning tool in `tools/provision_growcube.exe`;
- companion installation videos and images.

> GrowCube can only keep one active TCP connection. Before adding or searching for the device in Home Assistant, close the original app, control panel, or any other Home Assistant instance connected to the same GrowCube.

## 1. Install Home Assistant

If Home Assistant is not installed yet, follow the Windows + VirtualBox guide:

```text
INSTALL_HOME_ASSISTANT_WINDOWS_.md
```

The guide covers only Home Assistant installation and network setup. GrowCube itself is installed through HACS.

## 2. Install GrowCube With HACS

1. Open **HACS -> Integrations**.
2. Open the three-dot menu.
3. Choose **Custom repositories**.
4. Paste this repository URL:

   ```text
   https://github.com/lilbouu/GrowCube-HA
   ```

5. Select **Integration** as the category.
6. Add the repository.
7. Install **GrowCube**.
8. Restart Home Assistant.

HACS installs the integration from `custom_components/growcube`. The dashboard YAML and provisioning tool are included in this repository as additional setup files.

## 3. Connect GrowCube To Wi-Fi

Before adding GrowCube to Home Assistant, the cube must be connected to the same local network as Home Assistant.

### If GrowCube is already connected to Wi-Fi

You can add it from Home Assistant using automatic search or by entering its IP address manually.

If automatic search does not find the device, try entering your local subnet, for example:

```text
192.168.0.0/24
```

If you know the exact GrowCube IP address, enter that IP manually.

### If GrowCube is not connected to Wi-Fi yet

Use the Windows provisioning tool included in this repository:

```text
tools/provision_growcube.exe
```

The tool will:

1. ask you to connect Windows to the GrowCube setup Wi-Fi;
2. wait until Windows is connected to the GrowCube Wi-Fi;
3. scan available nearby Wi-Fi networks;
4. let you choose your home 2.4 GHz Wi-Fi network;
5. ask for the Wi-Fi password;
6. send the Wi-Fi setup command to GrowCube;
7. show the IP address reported by GrowCube after setup.

GrowCube supports 2.4 GHz Wi-Fi only. Do not choose a 5 GHz Wi-Fi network.

After provisioning succeeds, Windows may stay connected to the GrowCube setup Wi-Fi. Switch Windows back to your normal local Wi-Fi before searching for GrowCube in Home Assistant.

## 4. Add GrowCube In Home Assistant

1. Open **Settings -> Devices & services**.
2. Click **Add integration**.
3. Search for **GrowCube**.
4. Choose **Search automatically** or **Enter IP manually**.
5. Add the device.

Important: GrowCube can accept only one TCP connection at a time. If search or connection fails, make sure the original app, another Home Assistant instance, or a control panel is not connected to the cube.

## 5. Create The Dashboard

After adding the integration, create a Home Assistant dashboard and paste the YAML from:

```text
docs/lovelace-growcube-dashboard.yaml
```

The bundled custom card is served by the integration automatically.

## Extra Files

- `video_instruction/video.mp4` - silent companion video for the installation flow.
- `GrowCube_firmware/` - firmware-related files provided with this package.
