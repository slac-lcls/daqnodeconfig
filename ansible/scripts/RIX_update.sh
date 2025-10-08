#!/bin/bash
echo "sourcing setup_env.sh"
source ../../setup_env.sh
cd ../
echo "running ansible playbooks for RIX timing nodes"
ansible-playbook -K srcf_update.yml --tags timing -e variable_host=RIX_timing_drp
echo "running ansible playbooks for RIX hightiming nodes"
ansible-playbook -K srcf_update.yml --tags hightiming -e variable_host=RIX_hightiming_drp
echo "running ansible playbooks for RIX camera nodes"
ansible-playbook -K srcf_update.yml --tags camera -e variable_host=RIX_camera_drp
echo "running ansible playbooks for RIX hsd nodes"
ansible-playbook -K srcf_update.yml --tags hsd -e variable_host=RIX_hsd_drp
echo "running ansible playbooks for RIX wave8 nodes"
ansible-playbook -K srcf_update.yml --tags wave8 -e variable_host=RIX_wave8_drp