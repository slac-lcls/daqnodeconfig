# DAQ Node Config — Ansible Playbooks

This directory contains Ansible playbooks for deploying and maintaining the `datadev` PCIe driver,
FPGA firmware, and related system configuration on DAQ nodes at LCLS.

All playbooks require at minimum a `host` extra variable (space-separated Ansible inventory group
names or host patterns) unless noted otherwise.

---

## Playbooks

### `cfgRxCount_update.yaml`
Updates the `cfgRxCount` parameter in the `ExecStart` line of `/etc/systemd/system/datadev.service`
to a user-supplied value. After the replacement the driver is reloaded (`rmmod` → `daemon-reload` →
`start` → `enable`).  
**Requires:** `host`, `cfgRxCount`  
**Pre-condition:** `datadev.service` must already be installed on the node (run `files_update.yml`
first if not).

```bash
ansible-playbook cfgRxCount_update.yaml -K -e host='TMO_hsd_drp RIX_hsd_drp' -e cfgRxCount='6400'
```

---

### `distro_info.yml`
Queries and prints the Linux distribution name and version for each target host using Ansible
gather_facts. Useful for inventory auditing before deploying distribution-specific driver binaries.  
**Requires:** `host`

```bash
ansible-playbook distro_info.yml -e host='TMO_hsd_drp RIX_hsd_drp'
```

---

### `driver_reload.yml`
Reloads the `datadev` driver on nodes that already have `datadev.service` installed. Runs
`systemctl daemon-reload`, `start`, and `enable` without copying any new files.  
**Requires:** `host`  
**Pre-condition:** `/etc/systemd/system/datadev.service` must exist on the node.

```bash
ansible-playbook driver_reload.yml -K -e host='TMO_hsd_drp RIX_hsd_drp'
```

---

### `driver_update.yml`
Copies a pre-built `datadev.ko` kernel module from the controller to `/usr/local/sbin/datadev.ko`
on each target node. The correct binary is selected automatically from the controller's file store
based on the node's detected OS distribution and version. Triggers a driver reload if
`datadev.service` is already present.  
**Requires:** `host`, `driver` (version string, e.g. `6.9.0`)  
**Pre-condition:** The matching `datadev.ko_<driver>_<distro>_<version>` file must exist on the
controller under `/sdf/home/p/psrel/ansible_files/datadev/`.

```bash
ansible-playbook driver_update.yml -K -e host='TMO_hsd_drp' -e driver='6.9.0'
```

---

### `files_update.yml`
Full initial deployment playbook. Copies three files to each target node:
- `datadev.ko_<driver>_<distro>_<version>` → `/usr/local/sbin/datadev.ko`
- `<service>.service` → `/etc/systemd/system/datadev.service`
- `sysctl.conf` → `/etc/sysctl.d/90-daq.conf`

After copying, installs the driver (`rmmod` → `daemon-reload` → `start` → `enable`) and applies
the sysctl settings.  
**Requires:** `host`, `driver`, `service` (service template name, e.g. `kcu_hsd`)

```bash
ansible-playbook files_update.yml -K -e host='TMO_hsd_drp' -e driver='6.9.0' -e service='kcu_hsd'
```

---

### `firmware_update.yml`
Flashes FPGA firmware onto the PCIe card on each target node using `updatePcieFpga.py`. Supports a
built-in lookup table of firmware image names (see table below) or accepts a raw firmware path.
Detects whether `/proc/datadev_0` or `/proc/datadev_1` is present to select the correct device
node. Triggers an IPMI power reset after a successful flash.

| Key          | Firmware image |
|--------------|----------------|
| `hsd`        | `DrpPgpIlv-0x05040300-…` |
| `timing`     | `DrpTDet-0x05040300-…` |
| `timingC1100`| `DrpTDetGpuC1100-0x05040300-…` |
| `wave`       | `XilinxKcu1500Pgp4_6Gbps-0x02050000-…` |
| `camera`     | `Lcls2XilinxKcu1500Pgp2b-0x03100000-…` |
| `jungfrau`   | `Lcls2XilinxKcu1500Udp_10GbE-0x03040000-…` |
| `epix`       | `Lcls2XilinxKcu1500Pgp4_6Gbps-0x03080000-…` |

**Requires:** `host`, `firmware` (key from the table above, or a raw filename)  
**Pre-condition:** `datadev.service` must be installed (run `files_update.yml` first if not).

```bash
ansible-playbook firmware_update.yml -K -e host='TMO_hsd_drp' -e firmware='hsd'
```

---

### `limit_cfgRxCount.yml`
Safety-guard playbook. Reads the current `cfgRxCount` value from `datadev.service` and resets it
to `65532` if it exceeds that threshold. Reinstalls the driver after any change.  
**Requires:** `host`

```bash
ansible-playbook limit_cfgRxCount.yml -K -e host='TMO_hsd_drp RIX_hsd_drp'
```

---

### `master_update.yml`
Orchestrator playbook that groups nodes by device type and delegates to `srcf_manual.yml` via Ansible
tags. Defines presets for the following device types: `hsd`, `wave`, `camera`, `timing`,
`hightiming`, `jungfrau`. Each preset encodes the target host groups, driver version, service
template, and firmware key.

```bash
ansible-playbook master_update.yml --tags hsd
```

---

### `pci_ids_load.yml`
Copies a custom `pci.ids` database file to `/usr/share/hwdata/pci.ids` on target nodes so that
tools like `lspci` correctly identify DAQ PCIe hardware.  
**Requires:** `host`

```bash
ansible-playbook pci_ids_load.yml -K -e host='TMO_hsd_drp RIX_hsd_drp'
```

---

### `rebuild_driver.yml`
Builds the `datadev.ko` kernel module from source for the OS distribution and version detected on
each node. Runs **one node at a time** (`serial: 1`) to avoid concurrent build conflicts. If a
matching pre-built binary already exists in the safe folder, the build is skipped. The compiled
binary is saved to `/sdf/home/p/psrel/ansible_files/datadev/tmp/` for later use by deployment
playbooks.  
**Requires:** `host`, `driver` (git tag / branch to check out before building)

```bash
ansible-playbook rebuild_driver.yml -K -e host='TMO_hsd_drp RIX_hsd_drp' -e driver='6.9.0'
```

---

### `security_limits_modifier.yml`
Ensures the required PAM resource limits are set in `/etc/security/limits.conf` on Rocky Linux
nodes. Sets `memlock` to `unlimited` (soft and hard) and configures `core` dump limits as required
by the DAQ software.  
**Requires:** `host`  
**Note:** Only applies changes on Rocky Linux nodes.

```bash
ansible-playbook security_limits_modifier.yml -K -e host='TMO_hsd_drp'
```

---

### `servicefile_reinstall.yaml`
Restores `datadev.service` on each node from a per-hostname backup stored on the controller at
`/sdf/home/p/psrel/ansible_files/datadev_service_files/datadev.service.<hostname>`. Skips nodes
for which no backup exists. Targets the `rocky9` inventory group (no `host` variable needed).

```bash
ansible-playbook servicefile_reinstall.yaml -K
```

---

### `servicefile_single_update.yml`
Generic single-parameter updater for `datadev.service`. Replaces any named numeric parameter in
the `ExecStart` line with a new value. More flexible alternative to `cfgRxCount_update.yaml`.
Triggers a driver reload after the change.  
**Requires:** `host`, `var` (parameter name, e.g. `cfgRxCount`), `value` (new numeric value)  
**Pre-condition:** `datadev.service` must already be installed.

```bash
ansible-playbook servicefile_single_update.yml -K \
  -e host='TMO_hsd_drp' -e var='cfgRxCount' -e value='6400'
```

---

### `srcf_manual.yml`
Combined full-stack update playbook used as an include target by `master_update.yml`. Copies the
driver binary, service file, and sysctl config in one pass, then flashes FPGA firmware and triggers
an IPMI power reset. Contains the same firmware lookup table as `firmware_update.yml`.  
**Requires:** `host`, `driver`, `service`, `firmware`

```bash
ansible-playbook srcf_manual.yml -K \
  -e host='TMO_hsd_drp' -e driver='6.9.0' -e service='kcu_hsd' -e firmware='hsd'
```

---

### `sysctlOnly_update.yml`
Copies `sysctl.conf` from the controller to `/etc/sysctl.d/90-daq.conf` on target nodes and
immediately applies the settings with `sysctl -p`. Useful when only the kernel parameters need
updating without touching the driver or service files.  
**Requires:** `host`

```bash
ansible-playbook sysctlOnly_update.yml -K -e host='TMO_hsd_drp RIX_hsd_drp'
```

---

## Typical workflows

| Goal | Playbook(s) |
|------|-------------|
| Fresh node setup (driver + service + sysctl) | `files_update.yml` |
| Full stack update (driver + service + firmware) | `srcf_manual.yml` or `master_update.yml` |
| Flash new firmware only | `firmware_update.yml` |
| Update driver binary only | `driver_update.yml` |
| Reload driver after manual edit | `driver_reload.yml` |
| Change a single service parameter | `servicefile_single_update.yml` |
| Change `cfgRxCount` specifically | `cfgRxCount_update.yaml` |
| Cap `cfgRxCount` at safe limit | `limit_cfgRxCount.yml` |
| Restore service file from backup | `servicefile_reinstall.yaml` |
| Update sysctl settings only | `sysctlOnly_update.yml` |
| Update PCI device database | `pci_ids_load.yml` |
| Set PAM memory limits | `security_limits_modifier.yml` |
| Build driver from source | `rebuild_driver.yml` |
| Check node OS info | `distro_info.yml` |
