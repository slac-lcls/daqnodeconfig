# daqnodeconfig (v1.0.0)- Ansible Scripts for installing and updating nodes for DAQ use
In ~psrel/git/daqnodeconfig we have created an ansible playbook: "srcf_update.yml".
The code can be checked out from github:
https://github.com/slac-lcls/daqnodeconfig.git
git@github.com:slac-lcls/daqnodeconfig.git

The scope of the code is to:
copy essential files in pre-determined folders
vars:
  files_name:
  - {src: '../files/datadev.ko_(per system and per version)',    dest: '/usr/local/sbin/datadev.ko',                  mode: a+x}       
  - {src: '../files/kcu_(per detector).service', dest: '/usr/lib/systemd/system/datadev.service', mode: a+r}
  - {src: '../files/sysctl.conf',             dest: '/etc/sysctl.conf',                                   mode: a+r}
stop and desable kcu.service and tdetsim.service wherever they are still running
remove kcu.service and tdet.service from /etc/systemd/system/ folder
Whenever the files have changed, 
rmmod datadev
systemctl disable irqbalance.service
systemctl daemon-reload
systemctl start datadev.service
systemctl --force enable datadev.service

check and update firmware
defined in srcf_update.yml by :
firmware: DrpPgpIlv-0x05040300-20250811085728-weaver-846de54_primary

Whenever the firmware is updated, reset the node:
ipmitool power reset

Description of the content
One can use the psrel version or check out a private version.

We have divided the nodes in:
hsd_drp (RIX, TMO, all)
wave8_drp
camera_drp (RIX, TMO, all)
timing_drp (RIX, TMO, all)
hightiming_drp (RIX, TMO, all)
epixuhr_drp
epix100_drp
Jungfrau_drp

They can be found in the hosts file
  

there are several versions of the files in ansible/roles/drp_update/files:
datadev.ko_srcf
datadev.ko_srcf.6.6.1
kcu_epix.service
kcu_epix100.service
kcu_hsd.service
kcu_jung.service
kcu_wave.service
kcu.service
kcuSim
kcuStatus
tdetsim_high.service
tdetsim_slow.service
tdetsim.service

in ~psrel/mcs/drp we are going to store all the mcs files used so far. If one uses a locally cloned repository, ansible still looks for the psrel folder:

DrpPgpIlv-0x05030000-20250123122823-weaver-5f0f978_primary.mcs
DrpPgpIlv-0x05030000-20250123122823-weaver-5f0f978_secondary.mcs
DrpTDet-0x05000000-20240905155809-weaver-4a82bda_primary.mcs
DrpTDet-0x05000000-20240905155809-weaver-4a82bda_secondary.mcs
DrpTDet-0x05030000-20250123122727-weaver-5f0f978_primary.mcs
DrpTDet-0x05030000-20250123122727-weaver-5f0f978_secondary.mcs
Lcls2EpixHrXilinxKcu1500Pgp4_6Gbps-0x03020000-20250402173813-ruckman-68578d8_primary.mcs
Lcls2EpixHrXilinxKcu1500Pgp4_6Gbps-0x03020000-20250402173813-ruckman-68578d8_secondary.mcs
Lcls2XilinxC1100Pgp4_10Gbps-0x03000000-20220705135249-ruckman-42458f1.mcs
Lcls2XilinxC1100Pgp4_6Gbps-0x03000000-20220705135232-ruckman-42458f1.mcs
Lcls2XilinxKcu1500Pgp4_6Gbps-0x03080000-20240206001701-ruckman-9316db7_primary.mcs
Lcls2XilinxKcu1500Pgp4_6Gbps-0x03080000-20240206001701-ruckman-9316db7_secondary.mcs
Lcls2XilinxKcu1500Udp_10GbE-0x03010000-20250227102842-ruckman-9d02f81_primary.mcs
Lcls2XilinxKcu1500Udp_10GbE-0x03010000-20250227102842-ruckman-9d02f81_secondary.mcs
Lcls2XilinxKcu1500Udp_10GbE-0x03040000-20250314213558-ruckman-5042731_primary.mcs
Lcls2XilinxKcu1500Udp_10GbE-0x03040000-20250314213558-ruckman-5042731_secondary.mcs
XilinxKcu1500Pgp4_10Gbps-0x02050000-20240819125301-weaver-fe20e21_primary.mcs
XilinxKcu1500Pgp4_10Gbps-0x02050000-20240819125301-weaver-fe20e21_secondary.mcs
XilinxKcu1500Pgp4_10Gbps-0x02050000-20240819162750-weaver-02517bc_primary.mcs
XilinxKcu1500Pgp4_10Gbps-0x02050000-20240819162750-weaver-02517bc_secondary.mcs
XilinxVariumC1100Pgp4_15Gbps-0x02060000-20241030230014-ruckman-4a735d2.mcs


How to run>
source setup_env.sh in the main folder

go to ./ansible folder

run the script by

ansible-playbook -K srcf_update.yml (important uppercase -K for sudo password prompt)

it is possible to run in check mode on by using the option -c. This will simulate the install.

it is also possible to select a specific detector type instead of running the whole script by using --tags and one of the following:

hsd, wave8, camera, timing, hightiming, epixuhr, epix100, jungfrau (all lower case)
ansible-playbook -K srcf_update.yml --tags timing (or whatever you choose)

it is also possible to run a tag on a different host selection by using :

ansible-playbook -K srcf_update.yml -tags timing -e variable_hosts=TMO_timing_drp (for example)

NB be extremely careful because with this code it is possible to install a version on the nodes for another
Avoid mixing tags with variable_hosts: 
ansible-playbook -K srcf_update.yml -tags hsd -e variable_hosts=TMO_timing_drp 
Updates or changes
In the situation where one would need to make modifications, they can checkout a local version, SSH to a machine that can see the nodes, modify hosts, srcf_update.yml, or main.yml to configure the nodes the way they want.
For example: testing new firmware
In host define a new category of nodes [test_drp], in srcf_update.yml copy and paste from - hosts to -tags included and modify it to include files and firmware correct for your build. You can run run using the tags: 


