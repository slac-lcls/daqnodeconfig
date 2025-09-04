#!/bin/bash

echo Copying files
cp -u ../files/datadev.ko_srcf /usr/local/sbin/datadev.ko
chmod a+x /usr/local/sbin/datadev.ko

cp -u ../files/kcuSim /usr/local/sbin/kcuSim
chmod a+x /usr/local/sbin/kcuSim

cp -u ../files/kcuStatus /usr/local/sbin/kcuStatus
chmod a+x /usr/local/sbin/kcuStatus

cp -u ../files/tdetsim_low.service /usr/lib/systemd/system/tdetsim.service
chmod a+r /usr/lib/systemd/system/tdetsim.service

cp -u ../files/sysctl.conf /etc/sysctl.conf
chmod a+r /etc/sysctl.conf

echo Installing driver
rmmod datadev
systemctl disable irqbalance.service
systemctl daemon-reload 
systemctl start tdetsim.service
systemctl enable tdetsim.service
 
echo Check Firmware
firmware='DrpTDet-0x05000000-20240905155809-weaver-4a82bda_primary.mcs'
newest=$(ls -t /cds/home/p/psrel/mcs/drp/DrpTDet*.mcs | head -1)
if [ $newest != $firmware ];
    then echo New file found $newest
    read -rsn1 -p"Do you want to continue installing the older verions (y/n)" check;echo
    if [[ "$check" == "y" || "$check" == "Y" ]]; then
      echo "" # Add a newline for better formatting
  # Code to execute if Y is pressed
      echo "Continuing..."
    else
      echo "" # Add a newline for better formatting
  # Code to execute if any other key is pressed
      echo "Exiting..."
      echo "please modify script to update file"
      exit 1
    fi

fi

source ~tmoopr/daq/setup_env.sh
cd ~psrel/git/pgp-pcie-apps/software/
python ./scripts/nodePcieFpga.py --dev /dev/datadev_0  --filename ~psrel/mcs/drp/$firmware
