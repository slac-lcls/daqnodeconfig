#!/bin/bash
################
# NODE INSTALL
################
#
# to be used combined with git node default data
# This script will install into the desired node all the default files for the node
# call this script from drp-neh-ctl002 via a clush command
# clush --mode root -w drp-name node_install.sh
# store script path, where the script is run
# Author: melchior

PATH_SCRIPTS=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
NODE_TYPE1=$(cat /proc/datadev_0 | grep 'Build String' | cut -d ' ' -f 13)
NODE_TYPE=${NODE_TYPE1::-1} #this is the node type, which is used to activate the corresponding service
HOST=$(echo $(hostname)| cut -d'_' -f 2) # this is the system type, "fee" or "srcf"

input="$PATH_SCRIPTS/file_list.json"  # json file containing the information on the files and directories to be used

count=$(jq '.driver | length' "$input")

for ((i=0; i<$count; i++)); do
	FILENAME=$(jq -r .driver["$i"].driver_name "$input")  # name of the file
	INSTALL_FOLDER=$(jq -r .driver["$i"].install_path "$input") #folder where it should be installed
	ACTIVE=$(jq -r .driver["$i"].active "$input") # is it active? Should I copy it over? (True/False)
	echo "$PATH_SCRIPTS/$FILENAME" "$INSTALL_FOLDER" "$ACTIVE"
	if [ $ACTIVE == "True" ]; then # if active copy
		if [ $VARIABLE == "datadev.ko" ]; # datadev.ko depends on the system type
			then cp "$PATH_SCRIPTS/$VARIABLE_$HOST" "$INSTALL_FOLDER/$FILENAME"
		else
			cp "$PATH_SCRIPTS/$VARIABLE" "$INSTALL_FOLDER/"
	fi
done < "$input"

# allow to run services
chmod -x /usr/lib/systemd/system/tdetsim.service
chmod -x /usr/lib/systemd/system/kcu.service

# Stop irqbalance
systemctl disable irqbalance.service

# we loaded new a service, we need to reload the daemon before starting it
systemctl daemon-reload
# Start dedicated service 
if [ $NODE_TYPE == 'DrpTDet' ];then
	systemctl start tdetsim.service 
else
	systemctl start kcu.service
fi  
