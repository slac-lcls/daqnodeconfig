#!/bin/bash

ssh -X drp-neh-ctl002 clush -w drp-srcf-cmp0[01-50] ls /cds/drpsrcf/ > weka_drps.txt

