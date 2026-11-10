#!/usr/bin/env python3
#-----------------------------------------------------------------------------
# Company    : SLAC National Accelerator Laboratory
#-----------------------------------------------------------------------------
#  Description: Non-interactive BPI/SPIx4/SPIx8 PROM update for the DAQ nodes.
#
#  Based on axi-pcie-core v6.11.0 scripts/updatePcieFpga. Instead of choosing
#  from a list of images it takes the image with --filename and skips the
#  update when the card already runs that image, so it can be run from
#  ansible (firmware_update.yml, srcf_manual.yml).
#
#  usage (as root):
#    python nodePcieFpga.py --dev /dev/datadev_1 \
#        --filename /sdf/home/p/psrel/mcs/drp/DrpTDet-0x05040300-20250811085743-weaver-846de54 \
#        --rescan False
#
#  --filename can be the image base name, <base>_primary.mcs (KCU1500, two
#  PROMs, _secondary is found from it) or <base>.mcs (single PROM, e.g. C1100).
#
#  Output checked by the playbooks:
#    "Firmware already installed "  card already has the image, nothing written
#    "Installing new firmware"      PROM(s) are being written
#  Exit code is 0 on success or skip, 1 on any error (nothing written or the
#  programming failed), so the playbook does not power-cycle after an error.
#
#  RM 2024-08-19: Initial version (from axi-pcie-core v3.12.1 updatePcieFpga)
#  2026-11-09: rebased on axi-pcie-core v6.11.0, same safety checks (Claude)
#-----------------------------------------------------------------------------

import os
import re
import sys
import time
import glob
import argparse

baseDir = os.path.dirname(os.path.realpath(__file__))

# Where to look for the axipcie and surf python packages when they are not
# already in the python path: AXIPCIE_CORE_DIR, the axi-pcie-core checkout this
# script sits in (scripts/), then the psrel pgp-pcie-apps checkout
DEFAULT_CORE_DIR = '/sdf/home/p/psrel/git/pgp-pcie-apps/firmware/submodules/axi-pcie-core'

import pyrogue as pr
import rogue.hardware.axi

try:
    import axipcie as pcie
except ImportError:
    for coreDir in [os.environ.get('AXIPCIE_CORE_DIR'), baseDir + '/..', DEFAULT_CORE_DIR]:
        if coreDir and os.path.isdir(coreDir + '/python/axipcie'):
            pr.addLibraryPath(coreDir + '/python')
            pr.addLibraryPath(coreDir + '/../surf/python')
            break
    else:
        print('[ERROR]: axipcie python package not found, set AXIPCIE_CORE_DIR=<axi-pcie-core dir>')
        sys.exit(1)
    import axipcie as pcie

print(f"Basedir = {os.path.dirname(os.path.dirname(os.path.realpath(pcie.__file__)))}")

#################################################################

def check_if_need_rescan(dev):
    dev = dev.split('/')[-1]  # Strip path if needed
    try:
        with open(f'/proc/{dev}', "r", encoding='utf-8', errors='ignore') as file:
            for line in file:
                if 'Up Time Count' in line and '4294967295' in line:
                    return True
            return False
    except FileNotFoundError:
        return False

#################################################################

def get_pcie_address(dev):
    dev = dev.split('/')[-1]  # Strip path if needed
    try:
        with open(f'/proc/{dev}', "r", encoding='utf-8', errors='ignore') as file:
            pcie_address = ''
            for line in file:
                match = re.match(r'^PCIe\[BUS:NUM:SLOT.FUNC\]\s*:\s*(.*)', line)
                if match:
                    pcie_address = match.group(1).strip()
            return pcie_address
    except FileNotFoundError:
        return ''

#################################################################

def sorted_datadev_devices():
    # datadev_<index> (cfgDevName=0) or datadev_<bus> in hex (cfgDevName!=0),
    # reading the suffix as hex orders both
    def devIndex(path):
        match = re.fullmatch(r'datadev_([0-9a-fA-F]+)', os.path.basename(path))
        return (0, int(match.group(1), 16), '') if match else (1, 0, path)
    return sorted(glob.glob("/dev/datadev*"), key=devIndex)

#################################################################

def wait_for_datadev_devices(expected, timeout=30.0, period=0.5):
    # Hotplug slots remove and re-add the endpoint by themselves after
    # FpgaReload(), so poll until the device list comes back
    expected = set(expected)
    deadline = time.time() + timeout
    announced = False
    while True:
        current = set(sorted_datadev_devices())
        if current == expected:
            return True
        if time.time() >= deadline:
            missing = sorted(expected - current)
            extra   = sorted(current - expected)
            if missing:
                print(f"[WARNING] Devices missing after rescan: {missing}")
            if extra:
                print(f"[WARNING] Unexpected devices after rescan: {extra}")
            return False
        if not announced:
            print(f"Waiting up to {timeout:.0f}s for the datadev devices to return ....")
            announced = True
        time.sleep(period)

#################################################################

def image_files(filename, promType):
    # Returns (primary, secondary) for the PROM type, secondary is None for one PROM
    base = filename
    for suffix in ['_primary.mcs.gz', '_secondary.mcs.gz', '_primary.mcs', '_secondary.mcs', '.mcs.gz', '.mcs']:
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    for ext in ['mcs', 'mcs.gz']:
        if promType == 'SPIx8':
            pri, sec = f'{base}_primary.{ext}', f'{base}_secondary.{ext}'
            if os.path.isfile(pri) or os.path.isfile(sec):
                return pri, sec
        elif os.path.isfile(f'{base}.{ext}'):
            return f'{base}.{ext}', None
    if promType == 'SPIx8':
        return f'{base}_primary.mcs', f'{base}_secondary.mcs'
    return f'{base}.mcs', None

#################################################################

def image_id(path):
    # <Target>-0x<version>-<date>-<builder>-<hash>[...] -> (Target, version)
    match = re.match(r'^(.+?)-0x([0-9a-fA-F]+)-', os.path.basename(path))
    if match is None:
        return None, None
    return match.group(1), int(match.group(2), 16)

#################################################################

class Root(pr.Root):
    def __init__(self, dev='', **kwargs):
        super().__init__(pollEn=False, initRead=True, **kwargs)

        # Create the stream interface
        self.memMap = rogue.hardware.axi.AxiMemMap(dev)
        self.addInterface(self.memMap)

        # Add Base Device
        self.add(pcie.AxiPcieCore(
            memBase = self.memMap,
            useBpi  = True,
            useSpi  = True,
        ))

#################################################################

def load_mcs(prom, filename):
    try:
        prom._LoadMcsFile(filename, iprogPrint=False)
    except TypeError:
        prom._LoadMcsFile(filename)
    return prom._progDone

#################################################################

if __name__ == "__main__":

    # Set the argument parser
    parser = argparse.ArgumentParser()

    # Convert str to bool
    def argBool(s: str) -> bool:
        return s.lower() in ['true', 't', 'yes', '1']

    # Add arguments
    parser.add_argument(
        "--dev",
        type     = str,
        required = False,
        default  = "/dev/datadev_0",
        help     = "path to device",
    )

    parser.add_argument(
        "--filename",
        type     = str,
        required = True,
        help     = "image to program: base name, <base>_primary.mcs or <base>.mcs",
    )

    parser.add_argument(
        "--check",
        type     = argBool,
        required = False,
        default  = False,
        help     = "only report if the card needs a firmware update, write nothing",
    )

    parser.add_argument(
        "--force",
        type     = argBool,
        required = False,
        default  = False,
        help     = "program an image whose target differs from the one on the card",
    )

    parser.add_argument(
        "--rescan",
        type     = argBool,
        required = False,
        default  = True,
        help     = 'Rescan PCIe devices after update',
    )

    # Get the arguments
    args = parser.parse_args()

    # Check if args.dev does not exists
    if not os.path.exists(args.dev):
        print(f"\n[ERROR]: {args.dev} does NOT exists\n")
        sys.exit(1)

    # Check if PCIe endpoint needs rescan outside of this script
    if check_if_need_rescan(args.dev):
        print("\n\n[ERROR]: Unable to reprogram FPGA as lost of PCIe link.")
        print("Please reboot or rescan to recover\n")
        sys.exit(1)

    # Writing the PROM and the rescan need root
    pci_addr = get_pcie_address(args.dev)
    if (not args.check) and (os.geteuid() != 0):
        print("[ERROR]: This script must be run as root (sudo).")
        sys.exit(1)

    # The file name gives the target and version to compare with the card
    newTarget, newVersion = image_id(args.filename)
    if newTarget is None:
        print(f"[ERROR]: cannot read <Target>-0x<version> from {os.path.basename(args.filename)}")
        sys.exit(1)

    # Get a sorted list of all matching /dev/datadev* entries
    devices = sorted_datadev_devices()

    #################################################################

    with Root(dev=args.dev) as root:

        # Create useful pointers
        AxiVersion = root.AxiPcieCore.AxiVersion

        # Check if not the primary PCIe endpoint on the PCIe card
        if AxiVersion.BOOT_PROM_G.get() != 0xFFFFFFFF:
            promType   = AxiVersion.BOOT_PROM_G.getDisp()
        else:
            print("\n\n[ERROR]: No PROM type detected!!!")
            print("This is likely due to this PCIe interface not being the primary one on the PCIe card")
            print("Please use the primary interface with has the PROM type defined for reprogramming the card\n\n")
            sys.exit(1)

        # Case on PROM type
        if (promType == 'BPI'):
            PROM_PRI = root.AxiPcieCore.AxiMicronMt28ew
        elif (promType == 'SPIx8') or (promType == 'SPIx4'):
            PROM_PRI = root.AxiPcieCore.AxiMicronN25Q[0]
            if (promType == 'SPIx8'):
                PROM_SEC = root.AxiPcieCore.AxiMicronN25Q[1]
        else:
            print(f'[ERROR]: Invalid promType {promType}')
            sys.exit(1)

        # Printout Current AxiVersion status
        print('#########################################')
        print('Current Firmware Loaded on the PCIe card:')
        print('#########################################')
        AxiVersion.printStatus()
        print('#########################################')

        curImageName = AxiVersion.ImageName.get()
        curVersion   = AxiVersion.FpgaVersion.get()

        pri, sec = image_files(args.filename, promType)

        print(f'current {curImageName} {curVersion:#x}')
        print(f'new {os.path.basename(pri)}')

        if (newTarget == curImageName) and (newVersion == curVersion):
            print("Firmware already installed ")
            sys.exit(0)

        # Check the image files before anything is written
        for f in [pri, sec]:
            if f is not None and not os.path.isfile(f):
                print(f"\n[ERROR]: {f} not found (PROM type {promType})\n")
                sys.exit(1)

        # Programming another design onto the card needs --force
        if newTarget != curImageName:
            print(f"\n[ERROR]: image target {newTarget} does not match the card ({curImageName}), use --force True to program it anyway\n")
            sys.exit(1)

        if args.check:
            print("check set to 1, firmware is different")
            sys.exit(0)

        print("Installing new firmware")

        # Load the primary MCS file, then the secondary one if the card has two PROMs
        progDone = load_mcs(PROM_PRI, pri)
        if progDone and (promType == 'SPIx8'):
            progDone = load_mcs(PROM_SEC, sec)

        # Check if programming was successful
        if not progDone:
            print('[ERROR]: Failed to program FPGA')
            sys.exit(1)

        print('\nReloading FPGA firmware from PROM ....')
        AxiVersion.FpgaReload()

    #################################################################

    if args.rescan and (pci_addr != ''):

        def write_sysfs(path, value):
            try:
                with open(path, 'w') as f:
                    f.write(value)
            except Exception as e:
                print(f"Error writing to {path}: {e}")
                sys.exit(1)

        def unbind_device(pcieAddr):
            path = f"/sys/bus/pci/devices/{pcieAddr}/driver/unbind"
            if os.path.exists(path):
                print(f"Unbinding {pcieAddr}")
                write_sysfs(path, pcieAddr)
            else:
                print(f"No driver bound or invalid device: {pcieAddr}")

        def remove_device(pcieAddr):
            path = f"/sys/bus/pci/devices/{pcieAddr}/remove"
            if os.path.exists(path):
                print(f"Removing {pcieAddr} from PCI bus")
                write_sysfs(path, "1")
            else:
                print(f"Device {pcieAddr} not found in sysfs")

        def rescan_pci(pcieAddr):
            path = "/sys/bus/pci/rescan"
            print("Rescanning PCI bus\n")
            write_sysfs(path, "1")

        def rescan_top(pcieAddr):
            unbind_device(pcieAddr)
            time.sleep(1)

            remove_device(pcieAddr)
            time.sleep(1)

            rescan_pci(pcieAddr)
            time.sleep(1)

        # Allow for settling time for the FPGA reload to finish
        time.sleep(1)

        # Wait for hotplug slots to bring the devices back before the manual recovery
        devices_ok = wait_for_datadev_devices(devices)

        # Loop through all the devices
        rescanned = False
        for device in devices:

            # Check if we need to rescan device
            if check_if_need_rescan(device):

                # Perform the rescan procedure for given device
                rescanned = True
                rescan_top(get_pcie_address(device))

                # Wait for /dev node to return
                for i in range(10):
                    if os.path.exists(device):
                        time.sleep(1)  # Give driver time to initialize
                        break
                    time.sleep(0.5)
                else:
                    print(f"[WARNING] Device node {device} not found after rescan.")
                    print('\nPlease reboot the computer')
                    sys.exit(0)

        if rescanned:
            devices_ok = wait_for_datadev_devices(devices)

        # Print the new firmware string
        try:
            with Root(dev=args.dev) as root:
                print('#########################################')
                print('New Firmware Loaded on the PCIe card:')
                print('#########################################')
                root.AxiPcieCore.AxiVersion.printStatus()
                print('#########################################')
        except Exception as e:
            print(f"\n[WARNING] Unable to read {args.dev} after the FPGA reload: {e}")

        if not devices_ok:
            print("\n[WARNING] Mismatch between list of datadev devices before and after rescan")

    else:
        # Check if rescan and other driver
        if args.rescan and (pci_addr == ''):
            print('\nUnable to do rescan due to older driver')
            print('Update kernel driver to aes-stream-drivers@6.7.1 (other later) for this feature support\n')

    # The PROM is written, a reboot (ipmi reset in the playbooks) loads it everywhere
    print('\nPlease reboot the computer')
