#!/bin/bash

echo Copying files
cp ../files/datadev.ko_srcf /usr/local/sbin/datadev.ko
chmod a+x /usr/local/sbin/datadev.ko

cp ../files/kcuSim /usr/local/sbin/kcuSim
chmod a+x /usr/local/sbin/kcuSim

cp ../files/kcuStatus /usr/local/sbin/kcuStatus
chmod a+x /usr/local/sbin/kcuStatus

cp ../files/tdetsim_high.service /usr/lib/systemd/system/tdetsim.service
chmod a /usr/lib/systemd/system/tdetsim.service

cp ../files/sysctl.conf /etc/sysctl.conf
chmod a /etc/sysctl.conf

echo Installing driver
rmmod datadev
systemctl disable irqbalance.service
systemctl daemon-reload 
systemctl start tdetsim.service
systemctl enable tdetsim.service
 
echo Installing Firmware
source ~tmoopr/daq/setup_env.sh
cd ~psrel/git/pgp-pcie-apps/software/
python ./scripts/nodePcieFpga.py --dev /dev/datadev_0  --filename ~psrel/mcs/drp/DrpTDet-0x05000000-20240905155809-weaver-4a82bda_primary.mcs'
