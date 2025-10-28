#!/bin/bash
echo "sourcing setup_env.sh"
source ../../setup_env.sh
cd ../
echo "running ansible playbooks for TMO timing nodes"
ansible-playbook -K srcf_update.yml --tags timing -e variable_host=TMO_timing_drp
echo "running ansible playbooks for TMO hightiming nodes"
ansible-playbook -K srcf_update.yml --tags hightiming -e variable_host=TMO_hightiming_drp
echo "running ansible playbooks for TMO camera nodes"
ansible-playbook -K srcf_update.yml --tags camera -e variable_host=TMO_camera_drp
echo "running ansible playbooks for TMO hsd nodes"
ansible-playbook -K srcf_update.yml --tags hsd -e variable_host=TMO_hsd_drp
echo "running ansible playbooks for TMO wave8 nodes"
ansible-playbook -K srcf_update.yml --tags wave