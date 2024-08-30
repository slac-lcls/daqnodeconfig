#!/bin/bash

ssh -X drp-neh-ctl002 clush -w drp-srcf-cmp0[01-50] md5sum /usr/local/sbin/datadev.ko > md5_datadev.txt

