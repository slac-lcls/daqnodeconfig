#!/bin/bash
##############
# NODE CLONE
##############
#
# This script will clone all the default files for a node into another one
# Author: melchior

PATH_SCRIPTS=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd ) # Path where script is run

# need to provide 2 variables: from and to node drp name
if [ $# -ne 2 ]; 
    then echo "Please provide from and to dns names"
    exit
fi

# verify choice of drp nodes
echo "This will clone $1 into $2"
read -r -p "Are you sure? [y/N] " 
if [[ $REPLY =~ ^[Yy]$ ]];
then 
	#use clush to copy files from the node 1 to the node 2
	clush --mode sudo -w $1 "$PATH_SCRIPTS/node_copy.sh"
	clush --mode sudo -w $2 "$PATH_SCRIPTS/node_copy.sh -r"
        
fi
sudo rm -r "$PATH_SCRIPTS/DEFAULTS_TEMP/"
