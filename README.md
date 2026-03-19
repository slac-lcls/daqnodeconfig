DAQNODECONFIG is a collection of ansible playbooks to manage DAQ nodes.
The scripts are meant to cover different aspects of the administration of the nodes.

1 distro_info: It provides OS distribution and version for all host
  run: ansible-playbook distro_info.yml -e host='TMO_hsd_drp RIX_hsd_drp XPP_hsd_drp'
2 driver_reload: It only reloads a pre-existing driver, which means that datadev.ko and datadev.service must be present
  run: ansible-playbook driver_reload.yml -e host='TMO_hsd_drp RIX_hsd_drp XPP_hsd_drp'
3 driver_update: It updates the driver.ko only and reloads it using a pre-existing datadev.service
  it automatically detects the OS distribution and version and uses the corresponding datadev.ko
  run: ansible-playbook driver_update.yml -e host='TMO_hsd_drp RIX_hsd_drp XPP_hsd_drp' -e driver='6.9.0' 
4 files_update: It updates all files (datadev.ko, datadev.service, and sysctl.conf) and reloads the driver
  run: ansible-playbook files_update.yml -e host='TMO_hsd_drp RIX_hsd_drp XPP_hsd_drp' -e driver='6.9.0' -e service='kcu_hsd'
5 firmware_update: It updates the firmware of all host.
  Instead of defining the name of the firmware, the user must provide the type of the node to install the proper firmware.
  run: ansible-playbook firmware_update.yaml -e host='TMO_hsd_drp RIX_hsd_drp XPP_hsd_drp' -e firmware='hsd'
  one can also use firmware file name, withouth _primary.mcs
6 srcf_manual: It updates files, and firmware and loades the drivers
  run: ansible-playbook srcf_manual.yaml -e host='TMO_hsd_drp RIX_hsd_drp XPP_hsd_drp' -e driver='6.9.0' -e service='kcu_hsd' -e firmware='hsd'
7 master_update: It updates all nodes based on tags:
  run: ansible-playbook master_update.yml --tags hsd
8 rebuild_driver: It recreate datadev.ko per linux distribution
  run: ansible-playbook rebuild_driver.yaml -e host='TMO_hsd_drp RIX_hsd_drp XPP_hsd_drp' -e driver='6.9.0'

host can be replaced with DNS value, ie: drp-srcf-cmp001. BE AWARE, this works only if the DNS value is present in host file. If DNS value is unknown to host file, it will fail.
firmware types: hsd, timing, timingC1100, wave, camera, jungfrau, epix
