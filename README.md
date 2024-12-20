# daqnodeconfig - Ansible Scripts for installing and updating nodes for DAQ use
file tree:

>ansible
  ansible.cfg                     #ansible configuration file
  hosts                           #ansible file defining hosts
  roles
    drp_update
      files                       #contains the files to be copied in all nodes      tasks                       # main.yml contains the procedure to update an srcf node
  nodePcieFpga                    #modified firmware update file version copy in pgp-pcie-apps/firmware/submodules/axi-pcie-core/scripts/ and create link in pgp-pcie-apps/software/scripts or use default in "cd_cameralink" variable

>status
  find_hsd.sh               # creates a file HSD_drps.txt listing all drps having DrpPgpIlv in BuildString
  firmware_String.sh        # creates a file BuildString.txt listing all drp BuildString values
  md5sum_datadev.sh         # creates a file md5_datadev.txt listing the hash of every datadev.ko in srcf


In order to run ansible:
- activate conda
- source setup_env.sh
- in ansible folder run:
  ansible-playbook srcf_update.ym -K 
    sudo password is required



