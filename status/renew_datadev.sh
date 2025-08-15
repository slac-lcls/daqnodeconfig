#!/bin/bash

ssh drp-srcf-cmp001 "~melchior/git/aes-stream-drivers/make driver; cp -r /tmp/aes-stream-drivers/driver/datadev.ko ~melchior/git/default_node/daqnodeconfig/ansible/roles/drp_update/files/datadev.ko_6.7.1_srcf"
ssh drp-neh-cmp001 "~melchior/git/aes-stream-drivers/make driver; cp -r /tmp/aes-stream-drivers/driver/datadev.ko ~melchior/git/default_node/daqnodeconfig/ansible/roles/drp_update/files/datadev.ko_6.7.1_fee"
