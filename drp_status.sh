#!/bin/bash
write=0

while getopts ":w" option; do
   case $option in
      w) write=1 
         ;;
   esac
done

shift $((OPTIND-1))
 

if [ $write == 1 ]; then
    echo "######### Running clush on SRCF for datadev_0" > 'SRCF.dat'
    ssh -X drp-neh-ctl002 clush -w drp-srcf-cmp0[01-50] cat /proc/datadev_0|grep "Build String" >> 'SRCF.dat'
    echo "######### Running clush on SRCF for datadev_1">> 'SRCF.dat'
    ssh -X drp-neh-ctl002 clush -w drp-srcf-cmp0[01-50] cat /proc/datadev_1|grep "Build String">>'SRCF.dat'

    echo "######### Running clush on NEH for datadev_0" > 'NEH.dat'
    ssh -X drp-neh-ctl002 clush -w drp-neh-cmp0[01-30] cat /proc/datadev_0|grep "Build String" >> 'NEH.dat'
    echo "######### Running clush on NEH for datadev_1">> 'NEH.dat'
    ssh -X drp-neh-ctl002 clush -w drp-neh-cmp0[01-30] cat /proc/datadev_1|grep "Build String">>'NEH.dat'
else
    echo "######### Running clush on SRCF for datadev_0"
    ssh -X drp-neh-ctl002 clush -w drp-srcf-cmp0[01-50] cat /proc/datadev_0|grep "Build String"
    echo "######### Running clush on SRCF for datadev_1"
    ssh -X drp-neh-ctl002 clush -w drp-srcf-cmp0[01-50] cat /proc/datadev_1|grep "Build String"

    echo "######### Running clush on NEH for datadev_0"
    ssh -X drp-neh-ctl002 clush -w drp-neh-cmp0[01-30] cat /proc/datadev_0|grep "Build String"
    echo "######### Running clush on NEH for datadev_1"
    ssh -X drp-neh-ctl002 clush -w drp-neh-cmp0[01-30] cat /proc/datadev_1|grep "Build String"
fi
