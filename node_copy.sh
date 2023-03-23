#!/bin/bash
##############
# NODE COPY
##############
#
# This script will copy all the default files into and from a local folder of the node
# Author: melchior

#defines the direction of the copy of the node 0:from 1:to
reverse=0
while getopts ":r" option; do
   case $option in
      r) # display Help
         reverse=1
        ;;
   esac
done
# "copy to" creates a backup of the node
datedir="$(date +'%Y-%m-%d')"
# PATH where the script is run
PATH_SCRIPTS=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

HOST=$(hostname) # name of the drp node
input="$PATH_SCRIPTS/file_list.json" # json file conatining information about the files to be copied

NODE_TYPE1=$(cat /proc/datadev_0 | grep 'Build String' | cut -d ' ' -f 13)
NODE_TYPE=${NODE_LABEL1::-1}

if [ ! -d "$PATH_SCRIPTS/BACKUP_TEMP/" ]; then
  mkdir "$PATH_SCRIPTS/BACKUP_TEMP/"
fi 
datedir="$(date +'%Y-%m-%d')"
if [ ! -d "$PATH_SCRIPTS/BACKUP_TEMP/$datedir" ]; then
  mkdir "$PATH_SCRIPTS/BACKUP_TEMP/$datedir"
fi 
if [ ! -d "$PATH_SCRIPTS/DEFAULTS_TEMP/" ]; then
  mkdir "$PATH_SCRIPTS/DEFAULTS_TEMP/"  
fi     
if [ ! -d "$PATH_SCRIPTS/BACKUP_TEMP/$datedir/$HOST" ]; then
	mkdir "$PATH_SCRIPTS/BACKUP_TEMP/$datedir/$HOST"
fi 

count=$(jq '.driver | length' "$input")
if([ $reverse == 0 ]); then
	
	for ((i=0; i<$count; i++)); do

		FILENAME=$(jq -r .driver["$i"].driver_name "$input") # name of the file
		BACKUP_FOLDER=$(jq -r .driver["$i"].install_path "$input") # folder in which should be copied over in the node
		ACTIVE=$(jq -r .driver["$i"].active "$input") # do I want to copy it?

		echo "$BACKUP_FOLDER/$FILENAME" "$PATH_SCRIPTS/DEFAULTS_TEMP/"
		cp "$BACKUP_FOLDER/$FILENAME" "$PATH_SCRIPTS/DEFAULTS_TEMP/"

	done < "$input"
else
	if [ -d "$PATH_SCRIPTS/DEFAULTS_TEMP/" ]; then
		for ((i=0; i<$count; i++)); do

			FILENAME=$(jq -r .driver["$i"].driver_name "$input")
			BACKUP_FOLDER=$(jq -r .driver["$i"].install_path "$input")
			ACTIVE=$(jq -r .driver["$i"].active "$input")
			echo "$BACKUP_FOLDER$FILENAME" "$PATH_SCRIPTS/BACKUP_TEMP/$datedir/$HOST/"
			cp "$BACKUP_FOLDER/$FILENAME" "$PATH_SCRIPTS/BACKUP_TEMP/$datedir/$HOST/"
			echo "$PATH_SCRIPTS/DEFAULTS_TEMP/$FILENAME" "$BACKUP_FOLDER"
			cp "$PATH_SCRIPTS/DEFAULTS_TEMP/$FILENAME" "$BACKUP_FOLDER"
		done < "$input"

                # make services executable
	    	chmod -x /usr/lib/systemd/system/tdetsim.service
	    	chmod -x /usr/lib/systemd/system/kcu.service
                # disable irq balance
	    	systemctl disable irqbalance.service
                # new service has been installed, daemon needs to be reloaded before starting it
		systemctl daemon-reload
		# start the correct service per the node type
		if [ $NODE_TYPE == 'DrpTDet' ];then
			systemctl start tdetsim.service
    		else
      			systemctl start kcu.service
    		fi  
        
	else
		echo "There is no DEFAULTS_TEMP folder"	
	fi
fi
