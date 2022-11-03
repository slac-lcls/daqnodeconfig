from psdaq.configdb.get_config import get_config
from psdaq.configdb.scan_utils import *
from psdaq.configdb.typed_json import cdict
from psdaq.cas.xpm_utils import timTxId
#from .xpmmini import *
import pyrogue as pr
import rogue
import lcls2_epix_hr_pcie
import epix_hr_single_10k
import epix_hr_core as epixHr
import ePixFpga as fpga
import surf.protocols.batcher       as batcher
import time
import json
import os
import numpy as np
import IPython
from collections import deque
import logging

base = None
pv = None
#lane = 0  # An element consumes all 4 lanes
chan = None
group = None
ocfg = None
segids = None
seglist = [0,1]

elemRowsC = 146
elemRowsD = 144
elemCols  = 192

RTP = 6   # run trigger partition

def gain_mode_map(gain_mode):
    mapv  = (0xc,0xc,0x8,0x0,0x0)[gain_mode] # H/M/L/AHL/AML
    trbit = (0x1,0x0,0x0,0x1,0x0)[gain_mode]
    return (mapv,trbit)

class Board(pr.Root):
    def __init__(self,dev='/dev/datadev_0',lanes=4):
        super().__init__(name='ePixHr10kT',description='ePixHrGen1 board')
        self.dmaCtrlStreams = [None]
        self.dmaCtrlStreams[0] = rogue.hardware.axi.AxiStreamDma(dev,(0x100*0)+0,1)# Registers

        # Create and Connect SRP to VC1 to send commands
        self._srp = rogue.protocols.srp.SrpV3()
        pr.streamConnectBiDir(self.dmaCtrlStreams[0],self._srp)

        self.add(epixHr.SysReg  (name='Core'  , memBase=self._srp, offset=0x00000000, sim=False, expand=False, pgpVersion=4, numberOfLanes=lanes,))
        self.add(fpga.EpixHR10kT(name='EpixHR', memBase=self._srp, offset=0x80000000, hidden=False, enabled=True))
        #  Allow ePixFpga to find the yaml files for fnInitAsic()
        self.top_level = '/tmp'

def mode(a):
    uniqueValues = np.unique(a).tolist()
    uniqueCounts = [len(np.nonzero(a == uv)[0])
                    for uv in uniqueValues]

    modeIdx = uniqueCounts.index(max(uniqueCounts))
    return uniqueValues[modeIdx]

def dumpvars(prefix,c):
    print(prefix)
    for key,val in c.nodes.items():
        name = prefix+'.'+key
        dumpvars(name,val)

def retry(cmd,val):
    itry=0
    while(True):
        try:
            cmd(val)
        except Exception as e:
            logging.warning(f'Try {itry} of {cmd}({val}) failed.')
            if itry < 3:
                itry+=1
                continue
            else:
                raise e
        break

#
#  Apply the configuration dictionary to the rogue registers
#
def apply_dict(pathbase,base,cfg):
    rogue_translate = {}
    rogue_translate['TriggerEventBuffer0'] = 'TriggerEventBuffer[0]'
    rogue_translate['TriggerEventBuffer1'] = 'TriggerEventBuffer[1]'

    depth = 0
    my_queue  =  deque([[pathbase,depth,base,cfg]]) #contains path, dfs depth, rogue hiearchy, and daq configdb dict tree node
    while(my_queue):
        path,depth,rogue_node, configdb_node = my_queue.pop()
        if(dict is type(configdb_node)):
            for i in configdb_node:
                k = rogue_translate[i] if i in rogue_translate else i
                try:
                    my_queue.appendleft([path+"."+i,depth+1,rogue_node.nodes[k],configdb_node[i]])
                except KeyError:
                    logging.info('Lookup failed for node [{:}] in path [{:}]'.format(i,path))

        #  Apply
        if('get' in dir(rogue_node) and 'set' in dir(rogue_node) and path != pathbase ):
            if False:
                logging.info(f'NOT setting {path} to {configdb_node}')
            else:
                print(f'Setting {path} to {configdb_node}')
                #logging.info(f'Setting {path} to {configdb_node}')
                retry(rogue_node.set,configdb_node)

def _dict_compare(d1,d2,path):
    for k in d1.keys():
        if k in d2.keys():
            if d1[k] is dict:
                _dict_compare(d1[k],d2[k],path+'.'+k)
            elif (d1[k] != d2[k]):
                print(f'key[{k}] d1[{d1[k]}] != d2[{d2[k]}]')
        else:
            print(f'key[{k}] not in d1')
    for k in d2.keys():
        if k not in d1.keys():
            print(f'key[{k}] not in d2')

#
#  Construct an asic pixel mask with square spacing
#
def pixel_mask_square(value0,value1,spacing,position):
    ny,nx=288,384;
    if position>=spacing**2:
        logging.error('position out of range')
        position=0;
    out=np.zeros((ny,nx),dtype=np.int)+value0
    position_x=position%spacing; position_y=position//spacing
    out[position_y::spacing,position_x::spacing]=value1
    return out

#
#  Scramble the user element pixel array into the native asic orientation
#
#
#    A1   |   A3       (A1,A3) rotated 180deg
# --------+--------
#    A0   |   A2
#
def user_to_rogue(a):
    v = a.reshape((elemRowsD*2,elemCols*2))
    s = np.zeros((4,elemRowsC,elemCols),dtype=np.uint8)
    s[0,:elemRowsD] = v[elemRowsD:,:elemCols]
    s[2,:elemRowsD] = v[elemRowsD:,elemCols:]
    vf = np.flip(v)
    s[1,:elemRowsD] = vf[elemRowsD:,elemCols:]
    s[3,:elemRowsD] = vf[elemRowsD:,:elemCols]

    return s

#
#  Initialize the rogue accessor
#
def epixhr2x2_init(arg,dev='/dev/datadev_0',lanemask=1,xpmpv=None,timebase="186M",verbosity=0):
    global base
    global pv
#    logging.getLogger().setLevel(40-10*verbosity) # way too much from rogue
    logging.getLogger().setLevel(30)
    logging.warning('epixhr2x2_init')

    base = {}
    #  Configure the PCIe card first (timing, datavctap)
    pbase = lcls2_epix_hr_pcie.DevRoot(dev           =dev,
                                       enLclsI       =False,
                                       enLclsII      =False,
                                       yamlFileLclsI =None,
                                       yamlFileLclsII=None,
                                       startupMode   =True,
                                       standAloneMode=xpmpv is not None,
                                       pgp4          =True,
                                       #dataVc        =0,
                                       pollEn        =False,
                                       initRead      =False,
                                       #numLanes      =4,
                                       pcieBoardType = 'Kcu1500')
    pbase.__enter__()
    base['pci'] = pbase

    #  Connect to the camera
    elanes = 4 if timebase=="119M" else 2
    cbase = Board(dev=dev,lanes=elanes)
    cbase.__enter__()
    base['cam'] = cbase

    firmwareVersion = cbase.Core.AxiVersion.FpgaVersion.get()
    buildStamp = cbase.Core.AxiVersion.BuildStamp.get()
    gitHash = cbase.Core.AxiVersion.GitHash.get()
    print(f'firmwareVersion [{firmwareVersion:x}]')
    print(f'buildStamp      [{buildStamp}]')
    print(f'gitHash         [{gitHash:x}]')

    #  Enable the environmental monitoring
    cbase.EpixHR.SlowAdcRegisters.enable.set(1)
    cbase.EpixHR.SlowAdcRegisters.StreamPeriod.set(100000000)  # 1Hz
    cbase.EpixHR.SlowAdcRegisters.StreamEn.set(1)
    cbase.EpixHR.SlowAdcRegisters.enable.set(0)

   # configure timing
    if timebase=="119M":  # UED
        logging.warning('Using timebase 119M')
        base['bypass'] = 0x3f
        base['clk_period'] = 1000/119. 
        base['msg_period'] = 238
        base['pcie_timing'] = True

        logging.warning('epixhr2x2_unconfig')
        epixhr2x2_unconfig(base)

        pbase.DevPcie.Hsio.TimingRx.TimingFrameRx.ModeSelEn.set(1)
        pbase.DevPcie.Hsio.TimingRx.TimingFrameRx.ClkSel.set(0)
        pbase.DevPcie.Hsio.TimingRx.TimingFrameRx.RxDown.set(0)
    else:
        logging.warning('Using timebase 186M')
        base['bypass'] = 0x3b  # Need the first lanes for transitions
        base['clk_period'] = 7000/1300. # default 185.7 MHz clock
        base['msg_period'] = 200
        base['pcie_timing'] = False

        logging.warning('epixhr2x2_unconfig')
        epixhr2x2_unconfig(base)

        cbase.EpixHR.ConfigLclsTimingV2()

    # configure internal ADC
#    cbase.InitHSADC()

    time.sleep(1)
#    epixhr2x2_internal_trigger(base)
    return base

#
#  Set the PGP lane
#
def epixhr2x2_init_feb(slane=None,schan=None):
    global lane
    global chan
    if slane is not None:
        lane = int(slane)
    if schan is not None:
        chan = int(schan)

#
#  Set the local timing ID and fetch the remote timing ID
#
def epixhr2x2_connect(base):

#
#  To do:  get the IDs from the detector and not the timing link
#
    txId = timTxId('epixhr2x2')
    logging.warning('TxId {:x}'.format(txId))

    if base['pcie_timing'] and 'pci' in base:
        pbase = base['pci']
        rxId = pbase.DevPcie.Hsio.TimingRx.TriggerEventManager.XpmMessageAligner.RxId.get()
        pbase.DevPcie.Hsio.TimingRx.TriggerEventManager.XpmMessageAligner.TxId.set(txId)
    elif not base['pcie_timing'] and 'cam' in base:
        cbase = base['cam']
        rxId = cbase.EpixHR.TriggerEventManager.XpmMessageAligner.RxId.get()
        logging.warning('RxId {:x}'.format(rxId))
        cbase.EpixHR.TriggerEventManager.XpmMessageAligner.TxId.set(txId)
    else:
        rxId = 0xffffffff

    epixhrid = '-'

    d = {}
    d['paddr'] = rxId
    d['serno'] = epixhrid

    return d

#
#  Helper function for calling underlying pyrogue interface
#
def intToBool(d,types,key):
    if isinstance(d[key],dict):
        for k,value in d[key].items():
            intToBool(d[key],types[key],k)
    elif types[key]=='boolEnum':
        d[key] = False if d[key]==0 else True

def dictToYaml(d,types,keys,dev,path,name):
    v = {'enable':True}
    for key in keys:
        if key in d:
            v[key] = d[key]
            intToBool(v,types,key)
            v[key]['enable'] = True
        else:
            v[key] = {'enable':False}

    nd = {'ePixHr10kT':{'enable':True,'ForceWrite':False,'InitAfterConfig':False,'EpixHR':v}}
    yaml = pr.dataToYaml(nd)
    fn = path+name+'.yml'
    f = open(fn,'w')
    f.write(yaml)
    f.close()
    setattr(dev,'filename'+name,fn)

    #  Need to remove the enable field else Json2Xtc fails
    for key in keys:
        if key in d:
            del d[key]['enable']

#
#  Translate the 'user' components of the cfg dictionary into 'expert' settings
#  The cfg dictionary may be partial (scanning), so the ocfg dictionary is
#  reference for the full set.
#
def user_to_expert(base, cfg, full=False):
    global ocfg
    global group
    global lane

    d = {}
    hasUser = 'user' in cfg
    if (hasUser and 'start_ns' in cfg['user']):
        if base['pcie_timing']:
            pbase = base['pci']
            partitionDelay = getattr(pbase.DevPcie.Hsio.TimingRx.TriggerEventManager.XpmMessageAligner,'PartitionDelay[%d]'%group).get()
            rawStart       = cfg['user']['start_ns']
            triggerDelay   = int(rawStart/base['clk_period'] - partitionDelay*base['msg_period'])
            logging.warning('partitionDelay {:}  rawStart {:}  triggerDelay {:}'.format(partitionDelay,rawStart,triggerDelay))
            if triggerDelay < 0:
                logging.error('partitionDelay {:}  rawStart {:}  triggerDelay {:}'.format(partitionDelay,rawStart,triggerDelay))
                raise ValueError('triggerDelay computes to < 0')
            d[f'expert.DevPcie.Hsio.TimingRx.TriggerEventManager.TriggerEventBuffer0.TriggerDelay']=triggerDelay
            if full:
                d[f'expert.DevPcie.Hsio.TimingRx.TriggerEventManager.TriggerEventBuffer0.Partition']=group
        else:
            cbase = base['cam']
            for i,p in enumerate([RTP,group]):
                partitionDelay = getattr(cbase.EpixHR.TriggerEventManager.XpmMessageAligner,'PartitionDelay[%d]'%p).get()
                rawStart       = cfg['user']['start_ns']
                triggerDelay   = int(rawStart/base['clk_period'] - partitionDelay*base['msg_period'])
                logging.warning(f'partitionDelay[{p}] {partitionDelay}  rawStart {rawStart}  triggerDelay {triggerDelay}')
                if triggerDelay < 0:
                    logging.error(f'partitionDelay[{p}] {partitionDelay}  rawStart {rawStart}  triggerDelay {triggerDelay}')
                    raise ValueError('triggerDelay computes to < 0')

                d[f'expert.EpixHR.TriggerEventManager.TriggerEventBuffer{i}.TriggerDelay']=triggerDelay

            if full:
                d[f'expert.EpixHR.TriggerEventManager.TriggerEventBuffer0.Partition']=RTP
                d[f'expert.EpixHR.TriggerEventManager.TriggerEventBuffer1.Partition']=group

    pixel_map_changed = False
    a = None
    hasUser = 'user' in cfg
    if hasUser and ('pixel_map' in cfg['user'] or
                    'gain_mode' in cfg['user']):
        gain_mode = cfg['user']['gain_mode']
        if gain_mode==5:  # user map
            a  = cfg['user']['pixel_map']
            logging.warning('pixel_map len {}'.format(len(a)))
            d['user.pixel_map'] = a
            # what about associated trbit?
        else:
            mapv, trbit = gain_mode_map(gain_mode)
            for i in range(4):
                d[f'expert.EpixHR.Hr10kTAsic{i}.trbit'] = trbit
        pixel_map_changed = True

    update_config_entry(cfg,ocfg,d)

    return pixel_map_changed

#
#  Apply the cfg dictionary settings
#
def config_expert(base, cfg, writePixelMap=True, secondPass=False):

    #  Disable internal triggers during configuration
    epixhr2x2_external_trigger(base)

    pbase = base['pci']
    cbase = base['cam']

    # overwrite the low-level configuration parameters with calculations from the user configuration
    if 'expert' in cfg:
        if 'DevPcie' in cfg['expert']:
            apply_dict('pbase.DevPcie',pbase.DevPcie,cfg['expert']['DevPcie'])
        if not base['pcie_timing']:
            apply_dict('cbase.EpixHR.TriggerEventManager',cbase.EpixHR.TriggerEventManager,cfg['expert']['EpixHR']['TriggerEventManager'])

    epixHR = None
    if ('expert' in cfg and 'EpixHR' in cfg['expert']):
        epixHR = cfg['expert']['EpixHR'].copy()

    #  Make list of enabled ASICs
    asics = []
    if 'user' in cfg and 'asic_enable' in cfg['user']:
        for i in range(4):
            if cfg['user']['asic_enable']&(1<<i):
                asics.append(i)
            else:
                # remove the ASIC configuration so we don't try it
                del epixHR['Hr10kTAsic{}'.format(i)]
    else:
        asics = [i for i in range(4)]

    #  Set the application event builder for the set of enabled asics
    if base['pcie_timing']:
        m=3
        for i in asics:
            m = m | (4<<i)
    else:
        m=0
        for i in asics:
            m = m | (4<<int(i/2))
    base['bypass'] = 0x3f^m
    base['batchers'] = m>>2
    print('=== configure bypass {:x} ==='.format(base['bypass']))
    pbase.DevPcie.Application.EventBuilder.Bypass.set(base['bypass'])

    #  Use a timeout in AxiStreamBatcherEventBuilder
    #  Without a timeout, dropped contributions create an off-by-one between contributors
    pbase.DevPcie.Application.EventBuilder.Timeout.set(int(0.4e-3*156.25e6)) # 400 us
    if not base['pcie_timing']:
        eventBuilder = cbase.find(typ=batcher.AxiStreamBatcherEventBuilder)
        for eb in eventBuilder:
            eb.Timeout.set(int(0.4e-3*156.25e6))
            eb.Blowoff.set(True)

    #
    #  For some unknown reason, performing this part of the configuration on BeginStep
    #  causes the readout to fail until the next Configure
    #
    if epixHR is not None and not secondPass:
        # Work hard to use the underlying rogue interface
        # Translate config data to yaml files
        path = '/tmp/epixhr'
        epixHRTypes = cfg[':types:']['expert']['EpixHR']
        dictToYaml(epixHR,epixHRTypes,['MMCMRegisters'  ],cbase.EpixHR,path,'MMCM')
        dictToYaml(epixHR,epixHRTypes,['PowerSupply'    ],cbase.EpixHR,path,'PowerSupply')
        dictToYaml(epixHR,epixHRTypes,['RegisterControl'],cbase.EpixHR,path,'RegisterControl')
        for i in asics:
            dictToYaml(epixHR,epixHRTypes,['Hr10kTAsic{}'.format(i)],cbase.EpixHR,path,'ASIC{}'.format(i))
        # remove non-e xistent field
        for i in range(4):
            del epixHR[f'PacketRegisters{i}']['gainBitRemapped']
        dictToYaml(epixHR,epixHRTypes,['PacketRegisters{}'.format(i) for i in range(4)],cbase.EpixHR,path,'PacketReg')
        dictToYaml(epixHR,epixHRTypes,['TriggerRegisters'],cbase.EpixHR,path,'TriggerReg')

        arg = [1,0,0,0,0]
        for i in asics:
            arg[i+1] = 1
        logging.warning(f'Calling fnInitAsicScript(None,None,{arg})')
        cbase.EpixHR.fnInitAsicScript(None,None,arg) 
#        logging.warning(f'Calling fnInitAsic(None,None,{arg})')
#        cbase.EpixHR.fnInitAsic(None,None,arg)

#       Fixup the PacketRegisters0 disable register (lane 0 is broken?)
#        lanes = cbase.EpixHR.PacketRegisters0.DisableLane.get();
#        cbase.EpixHR.PacketRegisters0.DisableLane.set(lanes | 1);

    if writePixelMap:
        hasGainMode = 'gain_mode' in cfg['user']
        if (hasGainMode and cfg['user']['gain_mode']==5) or not hasGainMode:
            #
            #  Write the general pixel map
            #
            pixelConfigUsr = np.array(cfg['user']['pixel_map'],dtype=np.uint8)
            pixelConfigMap = user_to_rogue(pixelConfigUsr)

            for i in asics:
                #  Write a csv file then pass to rogue
                fn = path+'PixelMap{}.csv'.format(i)
                np.savetxt(fn, pixelConfigMap[i], fmt='%d', delimiter=',', newline='\n')
                print('Setting pixel bit map from {}'.format(fn))
                getattr(cbase.EpixHR,'Hr10kTAsic{}'.format(i)).fnSetPixelBitmap(None,None,fn)

            logging.warning('SetAsicsMatrix complete')
        else:
            #
            #  Write a uniform pixel map
            #
            gain_mode = cfg['user']['gain_mode']
            mapv, trbit = gain_mode_map(gain_mode)
            print(f'Setting uniform pixel map mode {gain_mode} mapv {mapv} trbit {trbit}')

            for i in asics:
                saci = getattr(cbase.EpixHR,f'Hr10kTAsic{i}')
                saci.enable.set(True)
                for b in range(48):
                    saci.PrepareMultiConfig()
                    saci.ColCounter.set(b)
                    saci.WriteColData.set(mapv)
                saci.CmdPrepForRead()
                saci.trbit.set(trbit)
                saci.enable.set(False)

    logging.warning('config_expert complete')

def reset_counters(base):
    # Reset the timing counters
    base['pci'].DevPcie.Hsio.TimingRx.TimingFrameRx.countReset()

    # Reset the trigger counters
    base['pci'].DevPcie.Hsio.TimingRx.TriggerEventManager.TriggerEventBuffer[1].countReset()

#
#  Called on Configure
#
def epixhr2x2_config(base,connect_str,cfgtype,detname,detsegm,rog):
    global ocfg
    global group
    global segids

    group = rog

#    _checkADCs()

    #
    #  Retrieve the full configuration from the configDB
    #
    cfg = get_config(connect_str,cfgtype,detname,detsegm)

    if not base['pcie_timing']:
        if 'TriggerEventManager' not in cfg['expert']['EpixHR']:
            #  Add missing fields
            f = {'Partition':'UINT32','PauseThreshold':'UINT32','TriggerDelay':'UINT32'}
            cfg[':types:']['expert']['EpixHR']['TriggerEventManager'] = {'TriggerEventBuffer0':f.copy(),
                                                                         'TriggerEventBuffer1':f.copy()}
            f = cfg['expert']['DevPcie']['Hsio']['TimingRx']['TriggerEventManager']['TriggerEventBuffer']
            cfg['expert']['EpixHR']['TriggerEventManager'] = {'TriggerEventBuffer0':f.copy(),
                                                              'TriggerEventBuffer1':f.copy()}
            #  Remove the old ones
            del cfg[':types:']['expert']['DevPcie']['Hsio']['TimingRx']['TriggerEventManager']['TriggerEventBuffer']
            del cfg['expert']['DevPcie']['Hsio']['TimingRx']['TriggerEventManager']['TriggerEventBuffer']

    ocfg = cfg

    #  Translate user settings to the expert fields
    writePixelMap=user_to_expert(base, cfg, full=True)

    #  Apply the expert settings to the device
    pbase = base['pci']
    cbase = base['cam']
    cbase.EpixHR.StopRun()
    pbase.StopRun()
    time.sleep(0.01)

    config_expert(base, cfg, writePixelMap)

    time.sleep(0.01)
    epixhr2x2_start(base)

    #  Add some counter resets here
    reset_counters(base)

    #  Enable triggers to continue monitoring
#    epixhr2x2_internal_trigger(base)

    #  Capture the firmware version to persist in the xtc
    cbase = base['cam']
    firmwareVersion = cbase.Core.AxiVersion.FpgaVersion.get()

    ocfg = cfg

    #
    #  Create the segment configurations from parameters required for analysis
    #
    trbit = [ cfg['expert']['EpixHR'][f'Hr10kTAsic{i}']['trbit'] for i in range(4)]

    topname = cfg['detName:RO'].split('_')

    scfg = {}
    segids = {}

    #  Rename the complete config detector
    scfg[0] = cfg.copy()
    scfg[0]['detName:RO'] = topname[0]+'hw_'+topname[1]


    #  User pixel map is assumed to be 288x384 in standard element orientation
    gain_mode = cfg['user']['gain_mode']
    if gain_mode==5:
        pixelConfigUsr = np.array(cfg['user']['pixel_map'],dtype=np.uint8)
    else:
        mapv,trbit = gain_mode_map(gain_mode)
        pixelConfigUsr = np.zeros((2*elemRowsD,2*elemCols),dtype=np.uint8)+mapv
    pixelConfigMap = user_to_rogue(pixelConfigUsr)

    for seg in range(1):
        #  Construct the ID
#        carrierId = [ cbase.SystemRegs.CarrierIdLow [seg].get(),
#                      cbase.SystemRegs.CarrierIdHigh[seg].get() ]
        carrierId = [ 0, 0 ]
        digitalId = [ 0, 0 ]
        analogId  = [ 0, 0 ]
        id = '%010d-%010d-%010d-%010d-%010d-%010d-%010d'%(firmwareVersion,
                                                          carrierId[0], carrierId[1],
                                                          digitalId[0], digitalId[1],
                                                          analogId [0], analogId [1])
        segids[seg] = id
        top = cdict()
        top.setAlg('config', [2,0,0])
        top.setInfo(detType='epixhr2x2', detName=topname[0], detSegm=seg+int(topname[1]), detId=id, doc='No comment')
        top.set('asicPixelConfig', pixelConfigUsr)
        top.set('trbit'          , [trbit for i in range(4)], 'UINT8')
        scfg[seg+1] = top.typed_json()

    result = []
    for i in seglist:
        logging.warning('json seg {}  detname {}'.format(i, scfg[i]['detName:RO']))
        result.append( json.dumps(scfg[i]) )

    return result

def epixhr2x2_unconfig(base):
    pbase = base['pci']
    cbase = base['cam']
    cbase.EpixHR.StopRun()
    pbase.StopRun()
    return base

#
#  Build the set of all configuration parameters that will change
#  in response to the scan parameters
#
def epixhr2x2_scan_keys(update):
    logging.warning('epixhr2x2_scan_keys')
    global ocfg
    global base
    global segids

    cfg = {}
    copy_reconfig_keys(cfg,ocfg,json.loads(update))
    # Apply to expert
    pixelMapChanged = user_to_expert(base,cfg,full=False)
    #  Retain mandatory fields for XTC translation
    for key in ('detType:RO','detName:RO','detId:RO','doc:RO','alg:RO'):
        copy_config_entry(cfg,ocfg,key)
        copy_config_entry(cfg[':types:'],ocfg[':types:'],key)

    topname = cfg['detName:RO'].split('_')

    scfg = {}

    #  Rename the complete config detector
    scfg[0] = cfg.copy()
    scfg[0]['detName:RO'] = topname[0]+'hw_'+topname[1]

    if pixelMapChanged:
        gain_mode = cfg['user']['gain_mode']
        if gain_mode==5:
            pixelConfigUsr = np.array(cfg['user']['pixel_map'],dtype=np.uint8)
        else:
            mapv,trbit = gain_mode_map(gain_mode)
            pixelConfigUsr = np.zeros((2*elemRowsD,2*elemCols),dtype=np.uint8)+mapv

        pixelConfigMap = user_to_rogue(pixelConfigUsr)
        trbit = [ cfg['expert']['EpixHR'][f'Hr10kTAsic{i}']['trbit'] for i in range(4)]

        cbase = base['cam']
        for seg in range(1):
            id = segids[seg]
            top = cdict()
            top.setAlg('config', [2,0,0])
            top.setInfo(detType='epixhr2x2', detName=topname[0], detSegm=int(topname[1]), detId=id, doc='No comment')
            top.set('asicPixelConfig', pixelConfigUsr)
            top.set('trbit'          , trbit                  , 'UINT8')
            scfg[seg+1] = top.typed_json()

    result = []
    for i in range(len(scfg)):
        result.append( json.dumps(scfg[i]) )

    return result

#
#  Return the set of configuration updates for a scan step
#
def epixhr2x2_update(update):
    logging.warning('epixhr2x2_update')
    global ocfg
    global base
    ##
    ##  Having problems with partial configuration
    ##
    # extract updates
    cfg = {}
    update_config_entry(cfg,ocfg,json.loads(update))
    #  Apply to expert
    writePixelMap = user_to_expert(base,cfg,full=False)
    #  Apply config
    #    config_expert(base, cfg, writePixelMap)
    ##
    ##  Try full configuration
    ##
#    ncfg = get_config(base['connect_str'],base['cfgtype'],base['detname'],base['detsegm'])
    ncfg = ocfg.copy()
    update_config_entry(ncfg,ocfg,json.loads(update))
    _writePixelMap = user_to_expert(base,ncfg,full=True)

    config_expert(base, ncfg, _writePixelMap, secondPass=True)

    epixhr2x2_start(base)

    #  Enable triggers to continue monitoring
#    epixhr2x2_internal_trigger(base)

    #  Retain mandatory fields for XTC translation
    for key in ('detType:RO','detName:RO','detId:RO','doc:RO','alg:RO'):
        copy_config_entry(cfg,ocfg,key)
        copy_config_entry(cfg[':types:'],ocfg[':types:'],key)

    topname = cfg['detName:RO'].split('_')

    scfg = {}

    #  Rename the complete config detector
    scfg[0] = cfg.copy()
    scfg[0]['detName:RO'] = topname[0]+'hw_'+topname[1]

    scfg[0] = cfg

    if writePixelMap:
        gain_mode = cfg['user']['gain_mode']
        if gain_mode==5:
            pixelConfigUsr = np.array(cfg['user']['pixel_map'],dtype=np.uint8)
        else:
            mapv,trbit = gain_mode_map(gain_mode)
            pixelConfigUsr = np.zeros((2*elemRowsD,2*elemCols),dtype=np.uint8)+mapv

        pixelConfigMap = user_to_rogue(pixelConfigUsr)
        try:
            trbit = [ cfg['expert']['EpixHR'][f'Hr10kTAsic{i}']['trbit'] for i in range(4)]
        except:
            trbit = None

        cbase = base['cam']
        for seg in range(1):
            id = segids[seg]
            top = cdict()
            top.setAlg('config', [2,0,0])
            top.setInfo(detType='epixhr2x2', detName=topname[0], detSegm=seg+int(topname[1]), detId=id, doc='No comment')
            top.set('asicPixelConfig', pixelConfigUsr)
            if trbit is not None:
                top.set('trbit'      , trbit                  , 'UINT8')
            scfg[seg+1] = top.typed_json()

    result = []
    for i in range(len(scfg)):
        result.append( json.dumps(scfg[i]) )

    logging.warning('update complete')

    return result

def _resetSequenceCount():
    cbase = base['cam']
    cbase.RegisterControl.ResetCounters.set(1)
    time.sleep(1.e6)
    cbase.RegisterControl.ResetCounters.set(0)

def epixhr2x2_external_trigger(base):
    #  Switch to external triggering
    print('=== external triggering with bypass {:x} ==='.format(base['bypass']))
    cbase = base['cam'].EpixHR
    cbase.TriggerRegisters.SetTimingTrigger(1)

def epixhr2x2_internal_trigger(base):
    #  Disable frame readout 
    mask = 0x3f if base['pcie_timing'] else 0x3b
    print('=== internal triggering with bypass {:x} ==='.format(mask))
    pbase = base['pci']
    pbase.DevPcie.Application.EventBuilder.Bypass.set(mask)
    return

    #  Switch to internal triggering
    print('=== internal triggering ===')
    cbase = base['cam'].EpixHR
    cbase.TriggerRegisters.SetAutoTrigger(1)

def epixhr2x2_enable(base):
    print('epixhr2x2_enable')
    epixhr2x2_external_trigger(base)

def epixhr2x2_disable(base):
    print('epixhr2x2_disable')
#    epixhr2x2_internal_trigger(base)

def epixhr2x2_start(base):
    pbase = base['pci']
    pbase.StartRun()
    cbase = base['cam']
    cbase.EpixHR.StartRun()
    m = base['batchers']
    cbase.EpixHR.BatcherEventBuilder0.Bypass.set(0)
    cbase.EpixHR.BatcherEventBuilder1.Bypass.set(0)
    cbase.EpixHR.BatcherEventBuilder2.Bypass.set(0)
    cbase.EpixHR.BatcherEventBuilder0.Blowoff.set((m&1)==0)
    cbase.EpixHR.BatcherEventBuilder1.Blowoff.set((m&2)==0)
    cbase.EpixHR.BatcherEventBuilder2.Blowoff.set((m&4)==0)
    print(f'Blowoff BatcherEventBuilders {m^0x7:x}')

#
#  Test standalone
#
if __name__ == "__main__":

    _base = epixhr2x2_init(None,dev='/dev/datadev_0')
    epixhr2x2_init_feb()
    epixhr2x2_connect(_base)

    db = 'https://pswww.slac.stanford.edu/ws-auth/devconfigdb/ws/configDB'
    d = {'body':{'control':{'0':{'control_info':{'instrument':'asc',
                                                 'cfg_dbase' :db}}}}}
    _connect_str = json.dumps(d)
    epixhr2x2_config(_base,_connect_str,'BEAM','epixhr',0,1)

    epixhr2x2_enable(_base)
