# DAQ Node Config — Ansible Playbooks

This directory contains Ansible playbooks for managing LCLS DAQ data-reduction-pipeline (DRP) nodes.
The playbooks handle firmware, driver, and service file deployment for PCIe-based detector readout cards
(KCU1500-class boards) across beamlines TMO, RIX, and XPP.

---

## Configuration Files

### `ansible.cfg`
Global Ansible configuration.

| Setting | Value | Purpose |
|---|---|---|
| `inventory` | `./hosts` | Points to the local hosts file |
| `remote_tmp` | `/tmp/.ansible_$USER/tmp` | Temp dir on remote nodes |
| `timeout` | 10 s | SSH connection timeout |
| `interpreter_python` | `/usr/bin/python3.9` | Python interpreter on nodes |
| `ssh_args` | ControlMaster with 60 s persist | Connection multiplexing for speed |

### `hosts`
Ansible inventory file. Defines named groups of DRP nodes (`drp-srcf-cmpXXX`, `drp-neh-cmpXXX`, etc.).

Key groups:

| Group | Nodes | Description |
|---|---|---|
| `TMO_hsd_drp` | cmp017–024, 046, 048, 050 | TMO HSD detector DRP nodes |
| `RIX_hsd_drp` | cmp005, cmp009 | RIX HSD detector DRP nodes |
| `XPP_hsd_drp` | cmp045, cmp052 | XPP HSD detector DRP nodes |
| `TMO_camera_drp` | cmp006, 007, 011, 013, 026 | TMO camera DRP nodes |
| `RIX_camera_drp` | cmp012, cmp027 | RIX camera DRP nodes |
| `wave_drp` | cmp004, 049, 051 | Waveform digitizer DRP nodes |
| `TMO_timing_drp` | cmp001 | TMO timing DRP nodes |
| `RIX_timing_drp` | cmp010, 025, 029 | RIX timing DRP nodes |
| `hightiming_drp` | cmp002, 003, 015, 016, 028, 030, 054 | High-rate timing DRP nodes |
| `Jungfrau_drp` | cmp032–035, 038, 039 | Jungfrau detector DRP nodes |
| `XPP_timing_drp` | cmp008 | XPP timing DRP node (C1100 card) |
| `Jungfrau_rocky9_drp` | cmp056 | Jungfrau detector DRP node (also covered by `master_update --tags jungfrau`) |
| `epix100_drp` | cmp031, 044 | ePix100 detector DRP nodes |
| `rocky9` | cmp001–080, mon001–019 | All Rocky Linux 9 nodes |
| `driver_drp` | cmp001–056 | Nodes requiring driver updates |

### `datadedev_version.txt`
A snapshot log (not a playbook) showing the `datadev.ko` file timestamps across nodes, used to verify driver deployment consistency. Last recorded: 2026-08-03.

---

## Playbook Reference

### Full-Stack Update Playbooks

#### `srcf_manual.yml` — Complete node update (driver + service + firmware + sysctl)
The most comprehensive single-playbook update. Copies all three configuration artifacts to a node and then flashes new firmware.

**Required variables:** `host`, `driver`, `service`, `firmware`

```bash
ansible-playbook srcf_manual.yml -K \
  -e host='TMO_hsd_drp RIX_hsd_drp' \
  -e driver='7.4.0' \
  -e service='kcu_hsd' \
  -e firmware='hsd'
```

**What it does:**
1. Copies `datadev.ko_<driver>_<distro>_<version>` → `/usr/local/sbin/datadev.ko`
2. Copies `<service>.service` → `/etc/systemd/system/datadev.service`
3. Copies `sysctl.conf` → `/etc/sysctl.d/40-datadev-sysctl.conf`
4. Determines which `/proc/datadev_X` device to use (prefers `datadev_1`)
5. Flashes FPGA firmware via `updatePcieFpga.py`
6. On firmware change: triggers `ipmitool power reset`
7. On file change: unloads `datadev`, reloads systemd, starts/enables the service, applies sysctl

**Built-in firmware aliases:**

| Key | Image |
|---|---|
| `hsd` | `DrpPgpIlv-0x05040300-20250811...` |
| `timing` | `DrpTDet-0x05040300-20250811...` |
| `timingC1100` | `DrpTDetGpuC1100-0x05040300-20250811...` |
| `wave` | `XilinxKcu1500Pgp4_6Gbps-0x02050000...` |
| `camera` | `Lcls2XilinxKcu1500Pgp2b-0x03100000...` |
| `jungfrau` | `Lcls2XilinxKcu1500Udp_10GbE-0x03040000...` |
| `epix` | `Lcls2XilinxKcu1500Pgp4_6Gbps-0x03080000...` |

A raw firmware path can also be passed; it will be prefixed with `drp/`.

---

#### `master_update.yml` — Orchestration wrapper for all device types
Defines per-device-type configuration (hosts, driver version, service name, firmware alias) and imports `srcf_manual.yml` once per device type, selected by tag. Running without `--tags` is refused, because `srcf_manual.yml` flashes firmware and power-cycles nodes whose firmware changed.

**Usage:** run with a tag to target one device type.

```bash
ansible-playbook master_update.yml -K --tags hsd
ansible-playbook master_update.yml -K --tags wave
ansible-playbook master_update.yml -K --tags camera
ansible-playbook master_update.yml -K --tags timing
ansible-playbook master_update.yml -K --tags hightiming
ansible-playbook master_update.yml -K --tags xpp_timing
ansible-playbook master_update.yml -K --tags jungfrau
```

**Device type definitions (all use driver 7.4.0).** Service files match what is installed on the nodes.

| Tag | Host groups | Service | Firmware |
|---|---|---|---|
| `hsd` | TMO/RIX/XPP_hsd_drp | `kcu_hsd` | `hsd` |
| `wave` | wave_drp except XPP_wave_drp (cmp049 runs kcu_xpm settings) | `kcu_wave` | `wave` |
| `camera` | TMO/RIX_camera_drp | `kcu` | `camera` |
| `timing` | TMO/RIX_timing_drp | `tdetsim_low` | `timing` |
| `hightiming` | TMO/RIX_hightiming_drp | `tdetsim_high` | `timing` |
| `xpp_timing` | XPP_timing_drp (cmp008, C1100 card) | `tdetsim_c1100` | `timingC1100` |
| `jungfrau` | Jungfrau_drp, Jungfrau_rocky9_drp | `kcu_jung` | `jungfrau` |

---

### Driver Playbooks

#### `files_update.yml` — First-time install of driver + service + sysctl
Use this when `datadev.service` is **not yet installed** on a node. Copies all three files and installs the driver.

**Required variables:** `host`, `driver`, `service`

```bash
ansible-playbook files_update.yml -K \
  -e host='TMO_hsd_drp' \
  -e driver='7.4.0' \
  -e service='kcu_hsd'
```

**Files deployed:**
- `datadev.ko_<driver>_<distro>_<version>` → `/usr/local/sbin/datadev.ko` (executable)
- `<service>.service` → `/etc/systemd/system/datadev.service`
- `sysctl.conf` → `/etc/sysctl.d/90-daq.conf`

Pre-checks verify both the driver `.ko` file and the service file exist on the controller before proceeding. On success: unloads old driver, daemon-reload, starts/enables service, applies sysctl.

---

#### `driver_update.yml` — Update driver on already-configured nodes
Use this when `datadev.service` **is already installed** and only the kernel module needs updating.

**Required variables:** `host`, `driver`

```bash
ansible-playbook driver_update.yml -K \
  -e host='TMO_hsd_drp RIX_hsd_drp XPP_hsd_drp' \
  -e driver='7.4.0'
```

Copies the version/distro-specific `.ko` to `/usr/local/sbin/datadev.ko`. Aborts with a clear message if `datadev.service` is not found (redirect to `files_update.yml`). On success: unloads old module, reloads systemd, starts/enables service.

---

#### `driver_reload.yml` — Reload driver without copying new files
Restarts the `datadev.service` without deploying any new files. Useful to recover after a manual change or to re-apply an existing driver.

**Required variables:** `host`

```bash
ansible-playbook driver_reload.yml -K \
  -e host='TMO_hsd_drp RIX_hsd_drp XPP_hsd_drp'
```

Aborts if `datadev.service` is not present (redirect to `files_update.yml`). Does **not** run `rmmod` (commented out).

---

#### `rebuild_driver.yml` — Build driver from source for a specific distro
Checks whether a pre-built driver for the node's distro/version already exists in the safe folder. If not, checks out the requested version tag from `aes-stream-drivers`, builds it, and copies the result to the safe folder. Runs **serially** (one node at a time) to prevent concurrent builds.

**Required variables:** `host`, `driver`

```bash
ansible-playbook rebuild_driver.yml -K \
  -e host='TMO_hsd_drp' \
  -e driver='7.4.0'
```

Key paths:
- Source repo: `/sdf/home/m/melchior/git/git/aes-stream-drivers`
- Build output: `.../data_dev/driver/datadev.ko`
- Safe folder: `/sdf/home/p/psrel/ansible_files/datadev/tmp`

---

### Firmware Playbook

#### `firmware_update.yml` — Update FPGA firmware only
Flashes new firmware to the PCIe card without touching driver or service files. Requires `datadev.service` to already be installed and the datadev device to be present at `/proc/datadev_0` or `/proc/datadev_1`.

**Required variables:** `host`, `firmware`

```bash
ansible-playbook firmware_update.yml -K \
  -e host='TMO_hsd_drp RIX_hsd_drp XPP_hsd_drp' \
  -e firmware='hsd'
```

Uses the same firmware alias table as `srcf_manual.yml`. Runs async (up to 300 s). On firmware change: triggers `ipmitool power reset`.

---

### Service File Playbooks

#### `servicefile_reinstall.yml` — Restore service files from per-host backups
Restores `datadev.service` on each node from a backup file named `datadev.service.<hostname>` stored on the controller. Targets the `rocky9` inventory group (no `host` variable needed).

```bash
ansible-playbook servicefile_reinstall.yml -K
```

Backup source: `/sdf/home/p/psrel/ansible_files/datadev_service_files/datadev.service.<hostname>`  
Destination: `/etc/systemd/system/datadev.service`

Skips nodes for which no backup file exists.

---

#### `backup_servicefile.yml` — Back up a file from each node to the controller
Fetches an arbitrary file from each target node and saves it to a per-host directory on the Ansible controller. Intended for backing up service files before making changes.

**Required variables:** `host`, `source_file`, `backup_root`

```bash
ansible-playbook backup_servicefile.yml \
  -e host='TMO_hsd_drp' \
  -e source_file='/etc/systemd/system/datadev.service' \
  -e backup_root='/sdf/home/p/psrel/ansible_files/backups'
```

Saves to: `<backup_root>/<hostname>/<hostname>_<basename_of_source_file>`

---

#### `servicefile_single_update.yml` — Update a single named parameter in the service file
Edits one `ExecStart` parameter in `/etc/systemd/system/datadev.service` by name, then reinstalls the driver service.

**Required variables:** `host`, `var` (parameter name), `value` (new value)

```bash
ansible-playbook servicefile_single_update.yml -K \
  -e host='TMO_hsd_drp' \
  -e var='cfgRxCount' \
  -e value='6400'
```

Uses a regex replace: `^(ExecStart=.*<var>=)\d+` → `\1<value>`. Aborts if `datadev.service` is not found.

---

### `cfgRxCount` Playbooks

#### `limit_cfgRxCount.yml` — Cap cfgRxCount at 65532
Safety guard: reads the current `cfgRxCount` in `datadev.service` and resets it to `65532` if the value exceeds that limit. Reinstalls the driver service on change.

**Required variables:** `host`

```bash
ansible-playbook limit_cfgRxCount.yml -K \
  -e host='TMO_hsd_drp RIX_hsd_drp'
```

---

### System Configuration Playbooks

#### `sysctlOnly_update.yml` — Deploy sysctl configuration only
Copies `sysctl.conf` from the controller to `/etc/sysctl.d/90-daq.conf` and applies it. Does not touch driver or service files.

**Required variables:** `host`

```bash
ansible-playbook sysctlOnly_update.yml -K \
  -e host='TMO_hsd_drp'
```

Source: `/sdf/home/p/psrel/ansible_files/sysctl.conf`

---

#### `security_limits_modifier.yml` — Configure `/etc/security/limits.conf`
Ensures DAQ-required memory-locking and core-dump limits are set. Only applies changes on **Rocky Linux** nodes.

**Required variables:** `host`

```bash
ansible-playbook security_limits_modifier.yml -K \
  -e host='TMO_hsd_drp'
```

Lines managed:

| Setting | Value |
|---|---|
| `* soft memlock` | `unlimited` |
| `* hard memlock` | `unlimited` |
| `* soft core` | `0` |
| `* hard core` | `unlimited` |

---

#### `pci_ids_load.yml` — Update the PCI ID database
Copies an updated `pci.ids` file to `/usr/share/hwdata/pci.ids` so that tools like `lspci` correctly identify PCIe devices.

**Required variables:** `host`

```bash
ansible-playbook pci_ids_load.yml -K \
  -e host='TMO_hsd_drp'
```

Source: `/sdf/home/p/psrel/ansible_files/pci.ids`

---

### Diagnostic Playbook

#### `distro_info.yml` — Report Linux distribution and version
Queries each target node for its OS distribution and version and prints a summary. Useful before running driver updates to confirm expected OS.

**Required variables:** `host`

```bash
ansible-playbook distro_info.yml \
  -e host='TMO_hsd_drp RIX_hsd_drp XPP_hsd_drp'
```

Output format: `<hostname>_<distro>_version_<version>` (e.g., `drp-srcf-cmp017_Rocky_version_9.4`)

---

## Typical Workflows

### New node (no datadev installed)
```bash
# 1. Check distro
ansible-playbook distro_info.yml -e host='<new_host>'

# 2. Install driver + service + sysctl
ansible-playbook files_update.yml -K \
  -e host='<new_host>' -e driver='7.4.0' -e service='kcu_hsd'

# 3. Flash firmware
ansible-playbook firmware_update.yml -K \
  -e host='<new_host>' -e firmware='hsd'
```

### Full update of existing nodes (all-in-one)
```bash
ansible-playbook srcf_manual.yml -K \
  -e host='TMO_hsd_drp' \
  -e driver='7.4.0' -e service='kcu_hsd' -e firmware='hsd'
```

### Driver-only update
```bash
ansible-playbook driver_update.yml -K \
  -e host='TMO_hsd_drp' -e driver='7.4.0'
```

### Update all device types at once
```bash
ansible-playbook master_update.yml -K --tags hsd
ansible-playbook master_update.yml -K --tags camera
# etc.
```

### Before modifying service files — back them up first
```bash
ansible-playbook backup_servicefile.yml \
  -e host='TMO_hsd_drp' \
  -e source_file='/etc/systemd/system/datadev.service' \
  -e backup_root='/sdf/home/p/psrel/ansible_files/backups/2026-09-08'
```

---

## Shared Steps and Handlers

The update playbooks are built from shared steps, so each one is the previous one plus more steps:

| Playbook | Steps |
|---|---|
| `driver_update.yml` | preflight, service_present, driver |
| `files_noservice_update.yml` | preflight, service_present, driver, sysctl, pci_ids |
| `files_update.yml` | preflight, driver, service, sysctl |
| `srcf_manual.yml` | preflight, driver, service, sysctl, firmware |
| `firmware_update.yml` | preflight, firmware |
| `sysctlOnly_update.yml` | preflight, sysctl |
| `pci_ids_load.yml` | preflight, pci_ids |

- `group_vars/all.yml` — paths, driver/service file naming, sysctl destination and the firmware list, defined once. Any value can be overridden with `-e`.
- `tasks/*.yml` — one step each. `preflight.yml` checks the variables listed in the playbook's `required_vars`.
- `handlers/datadev.yml` — `reload datadev` (rmmod, daemon-reload, start/enable `datadev.service`; skipped if the node has no `datadev.service`), `apply sysctl` (`sysctl -p /etc/sysctl.d/90-daq.conf`) and `ipmi reset` (`ipmitool power reset`, after new firmware). Handlers run once at the end of the play, so the driver is reloaded once and the reset is always last.

In `--check` mode the firmware step only prints the `updatePcieFpga.py` command it would run.

---

## Controller File Store — `/sdf/home/p/psrel/ansible_files/`

All source artifacts deployed by the playbooks live here. The tree is:

```
ansible_files/
├── datadev/                        # compiled kernel modules
│   ├── datadev.ko_<ver>_<distro>_<os_ver>
│   └── tmp/                        # staging area used by rebuild_driver.yml
├── datadev_service_files/          # per-host service file backups
│   └── datadev.service.<hostname>
├── services/                       # systemd unit file templates
│   ├── kcu_hsd.service
│   ├── kcu_hsd_card.service
│   ├── kcu_wave.service
│   ├── kcu_jung.service
│   ├── kcu_epix.service
│   ├── kcu_epix100.service
│   ├── kcu.service
│   ├── kcu_xpm.service
│   ├── tdetsim_high.service
│   └── tdetsim_low.service
├── sysctl.conf                     # kernel parameter tuning for DAQ
└── pci.ids                         # updated PCI device ID database
```

### `datadev/` — Compiled kernel modules

Naming convention: `datadev.ko_<driver_version>_<distro>_<os_version>`

| File | Driver | OS |
|---|---|---|
| `datadev.ko_6.6.1_redhat_7.1` | 6.6.1 | RHEL 7.1 |
| `datadev.ko_6.6.1_rocky_9.4` | 6.6.1 | Rocky 9.4 |
| `datadev.ko_6.6.1_rocky_9.6` | 6.6.1 | Rocky 9.6 |
| `datadev.ko_6.7.1_fee` | 6.7.1 | FEE nodes (special build) |
| `datadev.ko_6.7.1_rocky_9.4` | 6.7.1 | Rocky 9.4 |
| `datadev.ko_6.7.1_rocky_9.6` | 6.7.1 | Rocky 9.6 |
| `datadev.ko_6.7.1_srcf_rocky9` | 6.7.1 | SRCF Rocky 9 (special build) |
| `datadev.ko_6.8.0_redhat_7.9` | 6.8.0 | RHEL 7.9 |
| `datadev.ko_6.9.0_redhat_7.1` | 6.9.0 | RHEL 7.1 |
| `datadev.ko_6.9.0_redhat_7.9` | 6.9.0 | RHEL 7.9 |
| `datadev.ko_6.9.0_rocky_9.6` | 6.9.0 | Rocky 9.6 |
| `datadev.ko_6.12.0_rocky_9.6` | 6.12.0 | Rocky 9.6 |
| `datadev.ko_7.4.0_rocky_9.6` | 7.4.0 | Rocky 9.6 (current default, installed on the SRCF nodes) |
| `datadev.ko_fee` | — | Legacy FEE build |
| `datadev.ko_srcf` | — | Legacy SRCF build |
| `tmp/` | — | Staging dir for `rebuild_driver.yml` output |

The playbooks construct the filename at runtime using `ansible_distribution | lower` and `ansible_distribution_version` gathered from the target node, so the correct `.ko` is selected automatically per distro/version.

### `datadev_service_files/` — Per-host service file backups

Contains a saved copy of `/etc/systemd/system/datadev.service` from each node, used by `servicefile_reinstall.yml` to restore known-good configurations.

Nodes with backups: cmp001–cmp013, cmp017–cmp029, cmp031, cmp044, cmp046, cmp048, cmp050, cmp051, cmp055 (35 nodes total).

### `services/` — systemd unit file templates

These are the `datadev.service` variants deployed by `files_update.yml` and `srcf_manual.yml` via the `-e service=<name>` variable.

| File | Used for |
|---|---|
| `kcu_hsd.service` | HSD high-speed digitizer nodes |
| `kcu_hsd_card.service` | HSD standalone card nodes (`daq-*-hsd-*`) |
| `kcu_wave.service` | Waveform digitizer nodes |
| `kcu_jung.service` | Jungfrau detector nodes |
| `kcu_epix.service` | ePix detector nodes |
| `kcu_epix100.service` | ePix100 detector nodes |
| `kcu.service` | Generic KCU1500 nodes |
| `kcu_xpm.service` | XPM timing nodes |
| `tdetsim_high.service` | Timing detector (high-rate) nodes |
| `tdetsim_low.service` | Timing detector (low-rate) nodes |

### `sysctl.conf`

Kernel parameter tuning file applied to `/etc/sysctl.d/` on each node. Sets network buffer sizes and other parameters required for high-throughput DAQ operation. Deployed by `files_update.yml`, `srcf_manual.yml`, and `sysctlOnly_update.yml`.

### `pci.ids`

Updated PCI device ID database deployed to `/usr/share/hwdata/pci.ids` by `pci_ids_load.yml`. Ensures `lspci` and related tools correctly identify the KCU1500 and other PCIe cards on the nodes.

---

## Firmware Store — `/sdf/home/p/psrel/mcs/`

FPGA firmware images used by `firmware_update.yml` and `srcf_manual.yml`. Files follow the naming convention:

```
<design_name>-<fw_version_hex>-<build_date>-<builder>-<git_hash>[_primary|_secondary].mcs
```

- `_primary.mcs` / `_secondary.mcs` — dual-image flash: secondary is the active image, primary is the golden fallback loaded on JTAG recovery
- Single `.mcs` (no suffix) — single-image devices (e.g., C1100, EpixUHR)
- `.ltx` — Vivado ILA probe file for in-system debug (not deployed by playbooks)

---

### `drp/` — DRP (Data Reduction Pipeline) firmwares

These are the images referenced by the firmware aliases in `srcf_manual.yml` and `firmware_update.yml`.

#### HSD — `DrpPgpIlv` (PGP interleaved digitizer)

| Version | Build date | Status |
|---|---|---|
| `0x05030000` | 2025-01-23 | older |
| `0x05040200` | 2025-05-27 | older |
| `0x05040300` | 2025-08-11 | **current** (`hsd` alias) |

#### Timing detector — `DrpTDet`

| Version | Build date | Status |
|---|---|---|
| `0x05030000` | 2025-01-23 | older |
| `0x05040000` | 2025-04-28 | older |
| `0x05040200` | 2025-05-22 / 2025-05-27 | older |
| `0x05040300` | 2025-08-11 | **current** (`timing` alias) |

#### Timing detector GPU C1100 — `DrpTDetGpuC1100`

| Version | Build date | Status |
|---|---|---|
| `0x05040300` | 2025-08-11 | **current** (`timingC1100` alias) — single `.mcs` |

#### Camera link — `Lcls2XilinxKcu1500Pgp2b`

| Version | Build date | Status |
|---|---|---|
| `0x03100000` | 2025-01-14 | **current** (`camera` alias) |

#### Jungfrau — `Lcls2XilinxKcu1500Udp_10GbE`

| Version | Build date | Status |
|---|---|---|
| `0x03010000` | 2025-02-27 | older |
| `0x03040000` | 2025-03-14 | **current** (`jungfrau` alias) |

#### ePix — `Lcls2XilinxKcu1500Pgp4_6Gbps`

| Version | Build date | Status |
|---|---|---|
| `0x03080000` | 2024-02-06 | **current** (`epix` alias) |

#### ePix HR — `Lcls2EpixHrXilinxKcu1500Pgp4_6Gbps`

| Version | Build date | Status |
|---|---|---|
| `0x03020000` | 2025-04-02 | older |
| `0x03040000` | 2025-06-30 | latest |

#### C1100 cards

| Design | Version | Build date |
|---|---|---|
| `Lcls2XilinxC1100Pgp4_10Gbps` | `0x03000000` | 2022-07-05 |
| `Lcls2XilinxC1100Pgp4_6Gbps` | `0x03000000` | 2022-07-05 |
| `XilinxVariumC1100Pgp4_10Gbps` | `0x02110000` | 2025-06-20 |
| `XilinxVariumC1100Pgp4_15Gbps` | `0x02060000` | 2024-10-30 |

#### Wave / 10 Gbps PGP4 — `XilinxKcu1500Pgp4_10Gbps`

| Version | Build date |
|---|---|
| `0x02050000` | 2024-08-19 (two builds: `fe20e21`, `02517bc`) |

---

### `camlink/` — Camera link (dedicated subfolder)

| Design | Version | Build date |
|---|---|---|
| `Lcls2XilinxKcu1500Pgp2b` | `0x04030000` | 2026-08-19 |

Newer generation camera link firmware, stored separately from the `drp/` images.

---

### `EpixUHR/` — ePix Ultra-High-Rate readout

| Design | Version | Build date |
|---|---|---|
| `EpixUhrGTReadout` | `0x01000000` | 2024-10-01 |
| `EpixUhrGTReadout` | `0x01000000` | 2024-10-29 (two builds) |
| `EpixUhrGTReadout` | `0x01000000` | 2024-10-30 |

Single `.mcs` files (no primary/secondary split).

---

### `hsd/` — HSD 6400M digitizer card firmware

Firmware for the HSD 6400M card itself (distinct from the DRP PCIe readout firmware in `drp/`).

| Version | Build date |
|---|---|
| `0x06020000` | 2025-06-30 |
| `0x06020400` | 2025-08-29 |
| `0x06030000` | 2026-01-06 |
| `0x06040000` | 2026-05-31 |

---

### `wave8/` — Wave8 digitizer

| Design | Version | Build date |
|---|---|---|
| `Wave8DigitizerPgp4_6Gbps` | `0x02080100` | 2025-08-06 |

---

### `xpm/` — XPM timing system

Multiple design variants for the Cross-Platform Manager (XPM) timing hardware.

| Design | Version | Build date | Notes |
|---|---|---|---|
| `xpm` | `0x030C0302` | 2025-05-19 | bare XPM (AMC carrier) |
| `xpm` | `0x030D0300` | 2025-08-25 | |
| `xpm_noRTM` | `0x030C0302` | 2025-05-21 | XPM without RTM module |
| `xpm_noRTM` | `0x030D0300` | 2025-08-22 | |
| `xpmKcu1500` | `0x030C0302` | 2025-05-19 | XPM on KCU1500 PCIe card |
| `xpmKcu1500` | `0x030D0300` | 2025-08-25 | |
| `xpmGen` | `0x030D0300` | 2025-08-25 / 2025-08-28 | XPM generator variant |
| `xpmGenKcu1500` | `0x030C0302` | 2025-05-19 | XPM generator on KCU1500 |
| `xpmGenKcu1500` | `0x030D0300` | 2025-08-25 | |
| `xpmUed` | `0x030D0300` | 2025-08-25 | XPM variant for UED beamline |
| `xtpg` | `0x030C0302` | 2025-05-28 | timing pattern generator |
| `xtpg` | `0x030D0300` | 2025-08-25 | |
| `xtpg` | `0x030E0100` | 2026-03-18 | |

KCU1500 variants have `_primary.mcs` / `_secondary.mcs` pairs. AMC carrier variants are single `.mcs` files. `.ltx` files are present for some builds (ILA debug probes).

---

### Firmware alias → file mapping

The aliases used in `srcf_manual.yml` and `firmware_update.yml` resolve as follows:

| Alias | Full path under `/sdf/home/p/psrel/mcs/` |
|---|---|
| `hsd` | `drp/DrpPgpIlv-0x05040300-20250811085728-weaver-846de54` |
| `timing` | `drp/DrpTDet-0x05040300-20250811085743-weaver-846de54` |
| `timingC1100` | `drp/DrpTDetGpuC1100-0x05040300-20250811100751-weaver-846de54` |
| `wave` | `drp/XilinxKcu1500Pgp4_6Gbps-0x02050000-20240215192118-ruckman-3e991ff` |
| `camera` | `drp/Lcls2XilinxKcu1500Pgp2b-0x03100000-20250114180154-ruckman-524f66a` |
| `jungfrau` | `drp/Lcls2XilinxKcu1500Udp_10GbE-0x03040000-20250314213558-ruckman-5042731` |
| `epix` | `drp/Lcls2XilinxKcu1500Pgp4_6Gbps-0x03080000-20240206001701-ruckman-9316db7` |

The playbook appends `_primary.mcs` / `_secondary.mcs` is **not** done automatically — `updatePcieFpga.py` resolves the suffix internally based on the device slot.
