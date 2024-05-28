# daqnodeconfig - Scripts for installing and updating nodes for DAQ use

- node_install uses the folder where the script is to find the files to install them (copy and setup)
- node_copy copies the files from and to (need to be launched a second time)
- node_clone uses node_copy to perform a copy from and to another node
- drp-status uses clush on ctl002 (no need to be on ctl002) to read the Build String variable from both datadev_0 and _1  

node_copy also performs the install if in the direction "to" the node.

## Example

The following prepares <node(s)> when run from ctl002 in NEH.

```
clush --mode sudo -w <node/range> ~/lclsii/daq/daqnodeconfig/node_install.sh t
```
