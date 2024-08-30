# daqnodeconfig - Ansible Scripts for installing and updating nodes for DAQ use
file tree:

>ansible
  HSD
    HSD_driver_update.yml         #uses kcu.service or tdetsim.service to install driver
    HSD_file_chmod.yml            #in case permitions are wrong
    HSD_file_update.yml           #updates files in each HSD node
    HSD_firmware_update.yml       #updates drp firmware
    HSD_pcie_firmware_update.yml  #updates daq-tmo-hsd-01 cards firmware (make sure to kinit)
  
  ansible.cfg                     #ansible configuration file
  hosts                           #ansible file defining hosts
  nodePcieFpga                    #modified firmware update file version copy in pgp-pcie-apps/firmware/submodules/axi-pcie-core/scripts/ and create link in pgp-pcie-apps/software/scripts or use default in "cd_cameralink" variable

>shared_files
  datadev.ko_fee            # driver for FEE drp nodes
  datadev.ko_srcf           # driver for srcf nodes
  kcu_hsd.service           # service for HSD nodes
  kcu.service               # service for other nodes
  kcuSim                    # application for testing kcu
  kcuStatus                 # application for testing kcu
  sysctl.conf               # file needed to define system conditions
  tdetsim.service           # service for tdet drp nodes
  tdetsim.service_high      # service for high rate tdet drp nodes
  tdetsim.service_slow      # service for slow rate tdet drp nodes

>status
  find_hsd.sh               # creates a file HSD_drps.txt listing all drps having DrpPgpIlv in BuildString
  firmware_String.sh        # creates a file BuildString.txt listing all drp BuildString values
  md5sum_datadev.sh         # creates a file md5_datadev.txt listing the hash of every datadev.ko in srcf


In order to run ansible:
- activate conda
- conda activate /cds/sw/ds/dm/conda/envs/adm-0.2.0
- in ansible folder run:
  ansible-playbook HSD/HSD_XXXXX.yml -K 
  change XXXXX with desired script, -K if sudo password is required



