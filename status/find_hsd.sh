#!/bin/bash

ssh -X drp-neh-ctl002 clush -w drp-srcf-cmp0[01-50] cat /proc/datadev_1 | grep "Build String"| grep "DrpPgpIlv" > HSD_drps.txt

