# HACS Installation Notes

This folder is intended to be published as the root of the GitHub repository.

For HACS, the important files are:

```text
hacs.json
README.md
custom_components/growcube/
```

Optional supporting files included for users:

```text
docs/lovelace-growcube-dashboard.yaml
tools/provision_growcube.exe
INSTALL_HOME_ASSISTANT_WINDOWS_.md
video_instruction/
GrowCube_firmware/
```

## Customer Installation Flow

1. Add this GitHub repository to HACS as a custom repository.
2. Select repository type **Integration**.
3. Install **GrowCube** from HACS.
4. Restart Home Assistant.
5. Add the GrowCube integration from **Settings -> Devices & services**.
6. Create the dashboard from `docs/lovelace-growcube-dashboard.yaml`.

## Repository Requirement

Do not upload the parent development repository as-is if the customer should only see this package. Create the GitHub repository from the contents of this `GrowCube-HA` folder.
