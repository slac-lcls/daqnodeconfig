#!/bin/bash
#!/bin/bash

ssh -X drp-neh-ctl002 clush -w drp-srcf-cmp0[01-60] cat /proc/datadev_0 | grep "Build String" > BuildString.txt

