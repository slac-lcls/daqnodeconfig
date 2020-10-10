#include "PvaDetector.hh"

#ifdef NDEBUG
#undef NDEBUG
#endif

#include <getopt.h>
#include <cassert>
#include <bitset>
#include <chrono>
#include <unistd.h>
#include <sstream>
#include <iostream>
#include <map>
#include <algorithm>
#include <Python.h>
#include "DataDriver.h"
#include "RunInfoDef.hh"
#include "xtcdata/xtc/Damage.hh"
#include "xtcdata/xtc/DescData.hh"
#include "xtcdata/xtc/ShapesData.hh"
#include "xtcdata/xtc/NamesLookup.hh"
#include "psdaq/service/EbDgram.hh"
#include "psdaq/eb/TebContributor.hh"
#include "psalg/utils/SysLog.hh"

using json = nlohmann::json;
using logging = psalg::SysLog;


namespace Drp {

static const XtcData::Name::DataType xtype[] = {
  XtcData::Name::UINT8 , // pvBoolean
  XtcData::Name::INT8  , // pvByte
  XtcData::Name::INT16 , // pvShort
  XtcData::Name::INT32 , // pvInt
  XtcData::Name::INT64 , // pvLong
  XtcData::Name::UINT8 , // pvUByte
  XtcData::Name::UINT16, // pvUShort
  XtcData::Name::UINT32, // pvUInt
  XtcData::Name::UINT64, // pvULong
  XtcData::Name::FLOAT , // pvFloat
  XtcData::Name::DOUBLE, // pvDouble
  XtcData::Name::CHARSTR, // pvString
};

void PvaMonitor::getVarDef(XtcData::VarDef& varDef, size_t& payloadSize, size_t rankHack)
{
    std::string     name = "value";
    pvd::ScalarType type;
    size_t          nelem;
    size_t          rank;
    getParams(name, type, nelem, rank);

    if (rankHack != size_t(-1))  rank = rankHack; // Revisit: Hack!

    auto xtcType = xtype[type];
    varDef.NameVec.push_back(XtcData::Name(name.c_str(), xtcType, rank));

    payloadSize = nelem * XtcData::Name::get_element_size(xtcType);
}

void PvaMonitor::onConnect()
{
    logging::info("%s connected\n", name().c_str());

    if (m_para.verbose) {
        printStructure();
    }
}

void PvaMonitor::onDisconnect()
{
    logging::info("%s disconnected\n", name().c_str());
}

void PvaMonitor::updated()
{
    int64_t seconds;
    int32_t nanoseconds;
    getTimestamp(seconds, nanoseconds);
    XtcData::TimeStamp timestamp(seconds, nanoseconds);

    m_pvaDetector.process(timestamp);
}


class Pgp
{
public:
    Pgp(const Parameters& para, DrpBase& drp, const bool& running) :
        m_para(para), m_pool(drp.pool), m_tebContributor(drp.tebContributor()), m_running(running),
        m_available(0), m_current(0), m_lastComplete(0)
    {
        m_nodeId = drp.nodeId();
        uint8_t mask[DMA_MASK_SIZE];
        dmaInitMaskBytes(mask);
        for (unsigned i=0; i<4; i++) {
            if (para.laneMask & (1 << i)) {
                logging::info("setting lane  %d", i);
                dmaAddMaskBytes((uint8_t*)mask, dmaDest(i, 0));
            }
        }
        dmaSetMaskBytes(drp.pool.fd(), mask);
    }

    Pds::EbDgram* next(uint32_t& evtIndex, uint64_t& bytes);
private:
    Pds::EbDgram* _handle(uint32_t& evtIndex, uint64_t& bytes);
    const Parameters& m_para;
    MemPool& m_pool;
    Pds::Eb::TebContributor& m_tebContributor;
    static const int MAX_RET_CNT_C = 100;
    int32_t dmaRet[MAX_RET_CNT_C];
    uint32_t dmaIndex[MAX_RET_CNT_C];
    uint32_t dest[MAX_RET_CNT_C];
    const bool& m_running;
    int32_t m_available;
    int32_t m_current;
    uint32_t m_lastComplete;
    XtcData::TransitionId::Value m_lastTid;
    uint32_t m_lastData[6];
    unsigned m_nodeId;
};

Pds::EbDgram* Pgp::_handle(uint32_t& current, uint64_t& bytes)
{
    uint32_t size = dmaRet[m_current];
    uint32_t index = dmaIndex[m_current];
    uint32_t lane = (dest[m_current] >> 8) & 7;
    bytes += size;
    if (size > m_pool.dmaSize()) {
        logging::critical("DMA overflowed buffer: %d vs %d", size, m_pool.dmaSize());
        throw "DMA overflowed buffer";
    }

    const uint32_t* data = (uint32_t*)m_pool.dmaBuffers[index];
    uint32_t evtCounter = data[5] & 0xffffff;
    const unsigned bufferMask = m_pool.nbuffers() - 1;
    current = evtCounter & bufferMask;
    PGPEvent* event = &m_pool.pgpEvents[current];
    assert(event->mask == 0);

    DmaBuffer* buffer = &event->buffers[lane];
    buffer->size = size;
    buffer->index = index;
    event->mask |= (1 << lane);

    logging::debug("PGPReader  lane %u  size %u  hdr %016lx.%016lx.%08x",
                   lane, size,
                   reinterpret_cast<const uint64_t*>(data)[0],
                   reinterpret_cast<const uint64_t*>(data)[1],
                   reinterpret_cast<const uint32_t*>(data)[4]);

    const Pds::TimingHeader* timingHeader = reinterpret_cast<const Pds::TimingHeader*>(data);
    if (timingHeader->error()) {
        logging::error("Timing header error bit is set");
    }
    XtcData::TransitionId::Value transitionId = timingHeader->service();
    if (transitionId != XtcData::TransitionId::L1Accept) {
        logging::debug("PGPReader  saw %s transition @ %u.%09u (%014lx)",
                       XtcData::TransitionId::name(transitionId),
                       timingHeader->time.seconds(), timingHeader->time.nanoseconds(),
                       timingHeader->pulseId());
        if (transitionId == XtcData::TransitionId::BeginRun) {
            m_lastComplete = 0;  // EvtCounter reset
        }
    }
    if (evtCounter != ((m_lastComplete + 1) & 0xffffff)) {
        logging::critical("%sPGPReader: Jump in complete l1Count %u -> %u | difference %d, tid %s%s",
                          RED_ON, m_lastComplete, evtCounter, evtCounter - m_lastComplete, XtcData::TransitionId::name(transitionId), RED_OFF);
        logging::critical("data: %08x %08x %08x %08x %08x %08x",
                          data[0], data[1], data[2], data[3], data[4], data[5]);

        logging::critical("lastTid %s", XtcData::TransitionId::name(m_lastTid));
        logging::critical("lastData: %08x %08x %08x %08x %08x %08x",
                          m_lastData[0], m_lastData[1], m_lastData[2], m_lastData[3], m_lastData[4], m_lastData[5]);

        throw "Jump in event counter";

        for (unsigned e=m_lastComplete+1; e<evtCounter; e++) {
            PGPEvent* brokenEvent = &m_pool.pgpEvents[e & bufferMask];
            logging::error("broken event:  %08x", brokenEvent->mask);
            brokenEvent->mask = 0;

        }
    }
    m_lastComplete = evtCounter;
    m_lastTid = transitionId;
    memcpy(m_lastData, data, 24);

    event->l3InpBuf = m_tebContributor.allocate(*timingHeader, (void*)((uintptr_t)current));

    // make new dgram in the pebble
    // It must be an EbDgram in order to be able to send it to the MEB
    Pds::EbDgram* dgram = new(m_pool.pebble[current]) Pds::EbDgram(*timingHeader, XtcData::Src(m_nodeId), m_para.rogMask);

    return dgram;
}

Pds::EbDgram* Pgp::next(uint32_t& evtIndex, uint64_t& bytes)
{
    // get new buffers
    if (m_current == m_available) {
        m_current = 0;
        auto start = std::chrono::steady_clock::now();
        while (true) {
            m_available = dmaReadBulkIndex(m_pool.fd(), MAX_RET_CNT_C, dmaRet, dmaIndex, NULL, NULL, dest);
            if (m_available > 0) {
                m_pool.allocate(m_available);
                break;
            }

            // wait for a total of 10 ms otherwise timeout
            auto now = std::chrono::steady_clock::now();
            auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - start).count();
            if (elapsed > 10) {
                //if (m_running)  logging::debug("pgp timeout");
                return nullptr;
            }
        }
    }

    Pds::EbDgram* dgram = _handle(evtIndex, bytes);
    m_current++;
    return dgram;
}


PvaDetector::PvaDetector(Parameters& para, const std::string& pvDescriptor, DrpBase& drp) :
    XpmDetector(&para, &drp.pool),
    m_pvDescriptor(pvDescriptor),
    m_drp(drp),
    m_pgpQueue(drp.pool.nbuffers()),
    m_pvQueue(8),                       // Revisit size
    m_bufferFreelist(m_pvQueue.size()),
    m_terminate(false),
    m_running(false),
    m_firstDimKw(0)
{
    auto firstDimKw = para.kwargs["firstdim"];
    if (!firstDimKw.empty())
        m_firstDimKw = std::stoul(firstDimKw);
}

PvaDetector::~PvaDetector()
{
    // Try to take things down gracefully when an exception takes us off the
    // normal path so that the most chance is given for prints to show up
    shutdown();
}

unsigned PvaDetector::configure(const std::string& config_alias, XtcData::Xtc& xtc)
{
    logging::info("PVA configure");

    if (m_exporter)  m_exporter.reset();
    m_exporter = std::make_shared<Pds::MetricExporter>();
    if (m_drp.exposer()) {
        m_drp.exposer()->RegisterCollectable(m_exporter);
    }

    std::string provider = "pva";
    std::string pvName   = m_pvDescriptor;
    auto pos = m_pvDescriptor.find("/", 0);
    if (pos != std::string::npos) {
        provider = m_pvDescriptor.substr(0, pos);
        pvName   = pvName.substr(pos+1);
    }

    m_pvaMonitor = std::make_shared<PvaMonitor>(*m_para, pvName, *this, provider);

    std::string request = provider == "pva"
                        ? "field(value,timeStamp,dimension)"
                        : "field(value,timeStamp)";
    unsigned tmo = 3;
    bool ready = m_pvaMonitor->ready(request, tmo);
    if (!ready) {
        logging::error("Failed to connect with %s", m_pvaMonitor->name().c_str());
        return 1;
    }

    XtcData::Alg     rawAlg("raw", 1, 0, 0);
    XtcData::NamesId rawNamesId(nodeId, RawNamesIndex);
    XtcData::Names&  rawNames = *new(xtc) XtcData::Names(m_para->detName.c_str(), rawAlg,
                                                         m_para->detType.c_str(), m_para->serNo.c_str(), rawNamesId);
    size_t           payloadSize;
    XtcData::VarDef  rawVarDef;
    size_t           rankHack = m_firstDimKw != 0 ? 2 : -1; // Revisit: Hack!
    m_pvaMonitor->getVarDef(rawVarDef, payloadSize, rankHack);
    payloadSize += sizeof(Pds::EbDgram) + sizeof(XtcData::Shapes) + sizeof(XtcData::Shape);
    if (payloadSize > m_pool->pebble.bufferSize()) {
        logging::warning("Increase Pebble::m_bufferSize (%zd) to avoid truncation of %s data (%zd)",
                         m_pool->pebble.bufferSize(), m_pvaMonitor->name().c_str(), payloadSize);
    }
    rawNames.add(xtc, rawVarDef);
    m_namesLookup[rawNamesId] = XtcData::NameIndex(rawNames);

    XtcData::Alg     infoAlg("epicsinfo", 1, 0, 0);
    XtcData::NamesId infoNamesId(nodeId, InfoNamesIndex);
    XtcData::Names&  infoNames = *new(xtc) XtcData::Names("epicsinfo", infoAlg,
                                                          "epicsinfo", "detnum1234", infoNamesId);
    XtcData::VarDef  infoVarDef;
    infoVarDef.NameVec.push_back({"keys", XtcData::Name::CHARSTR, 1});
    infoVarDef.NameVec.push_back({m_para->detName.c_str(), XtcData::Name::CHARSTR, 1});
    infoNames.add(xtc, infoVarDef);
    m_namesLookup[infoNamesId] = XtcData::NameIndex(infoNames);

    // add dictionary of information for each epics detname above.
    // first name is required to be "keys".  keys and values
    // are delimited by ",".
    XtcData::CreateData epicsInfo(xtc, m_namesLookup, infoNamesId);
    epicsInfo.set_string(0, "epicsname"); //  "," "provider");
    epicsInfo.set_string(1, (m_pvaMonitor->name()).c_str()); // + "," + provider).c_str());

    size_t bufSize = m_pool->pebble.bufferSize();
    m_buffer.resize(m_pvQueue.size() * bufSize);
    for(unsigned i = 0; i < m_pvQueue.size(); ++i) {
        m_bufferFreelist.push(reinterpret_cast<XtcData::Dgram*>(&m_buffer[i * bufSize]));
    }

    m_terminate.store(false, std::memory_order_release);

    m_workerThread = std::thread{&PvaDetector::_worker, this};

    return 0;
}

void PvaDetector::event(XtcData::Dgram& dgram, PGPEvent* pgpEvent)
{
    XtcData::NamesId namesId(nodeId, RawNamesIndex);
    XtcData::DescribedData desc(dgram.xtc, m_namesLookup, namesId);
    auto payloadSize  = m_pool->pebble.bufferSize() - sizeof(Pds::EbDgram) - dgram.xtc.sizeofPayload() -
                        sizeof(XtcData::Shapes) - sizeof(XtcData::Shape);
    auto size         = payloadSize;
    auto shape        = m_pvaMonitor->getData(desc.data(), size);
    uint32_t shapeHack[XtcData::MaxRank]; // Revisit: Hack!
    if (m_firstDimKw != 0) {
      shapeHack[0] = m_firstDimKw;
      shapeHack[1] = shape[0] / m_firstDimKw;
    }
    if (size > payloadSize) {
        logging::debug("Truncated: Buffer of size %zu is too small for payload of size %zu for %s\n",
                       payloadSize, size, m_pvaMonitor->name().c_str());
        dgram.xtc.damage.increase(XtcData::Damage::Truncated);
        size = payloadSize;
    }
    desc.set_data_length(size);
    desc.set_array_shape(0, m_firstDimKw == 0 ? shape.data() : shapeHack); // Revisit: Hack!

    //size_t sz = (sizeof(dgram) + dgram.xtc.sizeofPayload()) >> 2;
    //uint32_t* payload = (uint32_t*)dgram.xtc.payload();
    //printf("sz = %zd, size = %zd, extent = %d, szofPyld = %d, pyldIdx = %ld\n", sz, size, dgram.xtc.extent, dgram.xtc.sizeofPayload(), payload - (uint32_t*)&dgram);
    //uint32_t* buf = (uint32_t*)&dgram;
    //for (unsigned i = 0; i < sz; ++i) {
    //  if (&buf[i] == (uint32_t*)&dgram)       printf(  "dgram:   ");
    //  if (&buf[i] == (uint32_t*)payload)      printf("\npayload: ");
    //  if (&buf[i] == (uint32_t*)desc.data())  printf("\ndata:    ");
    //  printf("%08x ", buf[i]);
    //}
    //printf("\n");
}

void PvaDetector::shutdown()
{
    m_terminate.store(true, std::memory_order_release);
    if (m_workerThread.joinable()) {
        m_workerThread.join();
    }
    m_pvaMonitor.reset();
    m_namesLookup.clear();   // erase all elements
}

void PvaDetector::_worker()
{
    // setup monitoring
    std::map<std::string, std::string> labels{{"instrument", m_para->instrument},
                                              {"partition", std::to_string(m_para->partition)},
                                              {"detname", m_para->detName},
                                              {"detseg", std::to_string(m_para->detSegment)},
                                              {"PV", m_pvaMonitor->name()}};
    m_nEvents = 0;
    m_exporter->add("drp_event_rate", labels, Pds::MetricType::Rate,
                    [&](){return m_nEvents;});
    uint64_t bytes = 0L;
    m_exporter->add("drp_pgp_byte_rate", labels, Pds::MetricType::Rate,
                    [&](){return bytes;});
    m_nUpdates = 0;
    m_exporter->add("pva_update_rate", labels, Pds::MetricType::Rate,
                    [&](){return m_nUpdates;});
    m_nMatch = 0;
    m_exporter->add("pva_match_count", labels, Pds::MetricType::Counter,
                    [&](){return m_nMatch;});
    m_nEmpty = 0;
    m_exporter->add("pva_empty_count", labels, Pds::MetricType::Counter,
                    [&](){return m_nEmpty;});
    m_nMissed = 0;
    m_exporter->add("pva_miss_count", labels, Pds::MetricType::Counter,
                    [&](){return m_nMissed;});
    m_nTooOld = 0;
    m_exporter->add("pva_tooOld_count", labels, Pds::MetricType::Counter,
                    [&](){return m_nTooOld;});
    m_nTimedOut = 0;
    m_exporter->add("pva_timeout_count", labels, Pds::MetricType::Counter,
                    [&](){return m_nTimedOut;});

    m_exporter->add("drp_worker_input_queue", labels, Pds::MetricType::Gauge,
                    [&](){return m_pgpQueue.guess_size();});
    m_exporter->constant("drp_worker_queue_depth", labels, m_pgpQueue.size());

    Pgp pgp(*m_para, m_drp, m_running);

    while (true) {
        if (m_terminate.load(std::memory_order_relaxed)) {
            break;
        }

        uint32_t index;
        Pds::EbDgram* dgram = pgp.next(index, bytes);
        if (dgram) {
            m_nEvents++;

            XtcData::TransitionId::Value service = dgram->service();
            // Also queue SlowUpdates to keep things in time order
            if ((service == XtcData::TransitionId::L1Accept) ||
                (service == XtcData::TransitionId::SlowUpdate)) {
                m_pgpQueue.push(index);

                _matchUp();

                // Prevent PGP events from stacking up by by timing them out.
                // If the PV is updating, _timeout() never finds anything to do.
                XtcData::TimeStamp timestamp;
                const unsigned msTmo = 100;
                const unsigned nsTmo = msTmo * 1000000;
                _timeout(timestamp.from_ns(dgram->time.to_ns() - nsTmo));
            }
            else {
                // Allocate a transition dgram from the pool and initialize its header
                Pds::EbDgram* trDgram = m_pool->allocateTr();
                memcpy((void*)trDgram, (const void*)dgram, sizeof(*dgram) - sizeof(dgram->xtc));
                // copy the temporary xtc created on phase 1 of the transition
                // into the real location
                XtcData::Xtc& trXtc = transitionXtc();
                memcpy((void*)&trDgram->xtc, (const void*)&trXtc, trXtc.extent);
                PGPEvent* pgpEvent = &m_pool->pgpEvents[index];
                pgpEvent->transitionDgram = trDgram;

                if (service == XtcData::TransitionId::Enable) {
                    m_running = true;
                }
                else if (service == XtcData::TransitionId::Disable) { // Sweep out L1As
                    m_running = false;
                    logging::debug("Sweeping out L1Accepts and SlowUpdates");
                    _timeout(dgram->time);
                }

                _sendToTeb(*dgram, index);
            }
        }
    }
    logging::info("Worker thread finished");
}

void PvaDetector::process(const XtcData::TimeStamp& timestamp)
{
    // Protect against namesLookup not being stable before Enable
    if (m_running.load(std::memory_order_relaxed)) {
        XtcData::Dgram* dgram;
        if (m_bufferFreelist.try_pop(dgram)) { // If a buffer is available...
            ++m_nUpdates;
            logging::debug("%s updated @ %u.%09u", m_pvaMonitor->name().c_str(), timestamp.seconds(), timestamp.nanoseconds());

            dgram->time = timestamp;           //   Save the PV's timestamp
            dgram->xtc = {{XtcData::TypeId::Parent, 0}, {nodeId}};

            event(*dgram, nullptr);            // PGPEvent not needed in this case

            m_pvQueue.push(dgram);
        }
        else {
            ++m_nMissed;                       // Else count it as missed
        }
    }
}

void PvaDetector::_matchUp()
{
    while (true) {
        XtcData::Dgram* pvDg;
        if (!m_pvQueue.peek(pvDg))  break;

        uint32_t pgpIdx;
        if (!m_pgpQueue.peek(pgpIdx))  break;

        Pds::EbDgram* pgpDg = reinterpret_cast<Pds::EbDgram*>(m_pool->pebble[pgpIdx]);

        logging::debug("PV: %u.%09d, PGP: %u.%09d, PGP - PV: %ld ns\n",
                       pvDg->time.seconds(), pvDg->time.nanoseconds(),
                       pgpDg->time.seconds(), pgpDg->time.nanoseconds(),
                       pgpDg->time.to_ns() - pvDg->time.to_ns());

        if      (pvDg->time == pgpDg->time)  _handleMatch  (*pvDg, *pgpDg);
        else if (pvDg->time >  pgpDg->time)  _handleYounger(*pvDg, *pgpDg);
        else                                 _handleOlder  (*pvDg, *pgpDg);
    }
}

void PvaDetector::_handleMatch(const XtcData::Dgram& pvDg, Pds::EbDgram& pgpDg)
{
    XtcData::Dgram* dgram;
    m_pvQueue.try_pop(dgram);           // Actually consume the element

    uint32_t pgpIdx;
    m_pgpQueue.try_pop(pgpIdx);         // Actually consume the element

    ++m_nMatch;
    logging::debug("PV matches PGP!!  "
                   "TimeStamps: PV %u.%09u == PGP %u.%09u",
                   pvDg.time.seconds(), pvDg.time.nanoseconds(),
                   pgpDg.time.seconds(), pgpDg.time.nanoseconds());

    if (pgpDg.service() == XtcData::TransitionId::L1Accept) {
        memcpy((void*)&pgpDg.xtc, (const void*)&pvDg.xtc, pvDg.xtc.extent);

        _sendToTeb(pgpDg, pgpIdx);
    }
    else { // SlowUpdate
        // Allocate a transition dgram from the pool and initialize its header
        Pds::EbDgram* trDg = m_pool->allocateTr();
        *trDg = pgpDg;
        PGPEvent* pgpEvent = &m_pool->pgpEvents[pgpIdx];
        pgpEvent->transitionDgram = trDg;

        memcpy((void*)&trDg->xtc, (const void*)&pvDg.xtc, pvDg.xtc.extent);

        _sendToTeb(*trDg, pgpIdx);
    }

    m_bufferFreelist.push(dgram);     // Return buffer to freelist
}

void PvaDetector::_handleYounger(const XtcData::Dgram& pvDg, Pds::EbDgram& pgpDg)
{
    uint32_t pgpIdx;
    m_pgpQueue.try_pop(pgpIdx);       // Actually consume the element

    if (pgpDg.service() == XtcData::TransitionId::L1Accept) {
        // No corresponding PV data so mark event damaged
        pgpDg.xtc.damage.increase(XtcData::Damage::MissingData);

        ++m_nEmpty;

        logging::debug("PV too young!!    "
                       "TimeStamps: PV %u.%09u > PGP %u.%09u",
                       pvDg.time.seconds(), pvDg.time.nanoseconds(),
                       pgpDg.time.seconds(), pgpDg.time.nanoseconds());
    }
    else {
        // Allocate a transition dgram from the pool and initialize its header
        Pds::EbDgram* trDg = m_pool->allocateTr();
        *trDg = pgpDg;
        PGPEvent* pgpEvent = &m_pool->pgpEvents[pgpIdx];
        pgpEvent->transitionDgram = trDg;
    }

    _sendToTeb(pgpDg, pgpIdx);
}

void PvaDetector::_handleOlder(const XtcData::Dgram& pvDg, Pds::EbDgram& pgpDg)
{
    XtcData::Dgram* dgram;
    m_pvQueue.try_pop(dgram);           // Actually consume the element

    ++m_nTooOld;
    logging::debug("PV too old!!      "
                   "TimeStamps: PV %u.%09u < PGP %u.%09u",
                   pvDg.time.seconds(), pvDg.time.nanoseconds(),
                   pgpDg.time.seconds(), pgpDg.time.nanoseconds());

    m_bufferFreelist.push(dgram);       // Return buffer to freelist
}

void PvaDetector::_timeout(const XtcData::TimeStamp& timestamp)
{
    while (true) {
        uint32_t index;
        if (!m_pgpQueue.peek(index)) {
            break;
        }

        Pds::EbDgram& dgram = *reinterpret_cast<Pds::EbDgram*>(m_pool->pebble[index]);
        if (dgram.time > timestamp) {
            break;                  // dgram is newer than the timeout timestamp
        }

        uint32_t idx;
        m_pgpQueue.try_pop(idx);        // Actually consume the element
        assert(idx == index);

        // No PVA data so mark event as damaged
        dgram.xtc.damage.increase(XtcData::Damage::TimedOut);
        ++m_nTimedOut;
        logging::debug("Event timed out!! "
                       "TimeStamps: timeout %u.%09u > PGP %u.%09u",
                       timestamp.seconds(), timestamp.nanoseconds(),
                       dgram.time.seconds(), dgram.time.nanoseconds());


        if (dgram.service() != XtcData::TransitionId::SlowUpdate) {
            _sendToTeb(dgram, index);
        }
        else {
            // Allocate a transition dgram from the pool and initialize its header
            Pds::EbDgram* trDgram = m_pool->allocateTr();
            *trDgram = dgram;
            PGPEvent* pgpEvent = &m_pool->pgpEvents[index];
            pgpEvent->transitionDgram = trDgram;

            _sendToTeb(*trDgram, index);
        }
    }
}

void PvaDetector::_sendToTeb(const Pds::EbDgram& dgram, uint32_t index)
{
    // Make sure the datagram didn't get too big
    const size_t size = sizeof(dgram) + dgram.xtc.sizeofPayload();
    const size_t maxSize = ((dgram.service() == XtcData::TransitionId::L1Accept) ||
                            (dgram.service() == XtcData::TransitionId::SlowUpdate))
                         ? m_pool->pebble.bufferSize()
                         : m_para->maxTrSize;
    if (size > maxSize) {
        logging::critical("%s Dgram of size %zd overflowed buffer of size %zd", XtcData::TransitionId::name(dgram.service()), size, maxSize);
        throw "Dgram overflowed buffer";
    }

    PGPEvent* event = &m_pool->pgpEvents[index];
    if (event->l3InpBuf) { // else shutting down
        Pds::EbDgram* l3InpDg = new(event->l3InpBuf) Pds::EbDgram(dgram);
        if (l3InpDg->isEvent()) {
            if (m_drp.triggerPrimitive()) { // else this DRP doesn't provide input
                m_drp.triggerPrimitive()->event(*m_pool, index, dgram.xtc, l3InpDg->xtc); // Produce
            }
        }
        m_drp.tebContributor().process(l3InpDg);
    }
    else {
        logging::error("Attempted to send to TEB without an Input buffer");
    }
}


PvaApp::PvaApp(Parameters& para, const std::string& pvDescriptor) :
    CollectionApp(para.collectionHost, para.partition, "drp", para.alias),
    m_drp(para, context()),
    m_para(para),
    m_det(std::make_unique<PvaDetector>(m_para, pvDescriptor, m_drp))
{
    if (m_det == nullptr) {
        logging::critical("Error !! Could not create Detector object for %s", m_para.detType.c_str());
        throw "Could not create Detector object for " + m_para.detType;
    }
    if (m_para.outputDir.empty()) {
        logging::info("output dir: n/a");
    } else {
        logging::info("output dir: %s", m_para.outputDir.c_str());
    }
    logging::info("Ready for transitions");
}

PvaApp::~PvaApp()
{
    // Try to take things down gracefully when an exception takes us off the
    // normal path so that the most chance is given for prints to show up
    handleReset(json({}));
}

void PvaApp::_shutdown()
{
    _unconfigure();
    _disconnect();
}

void PvaApp::_disconnect()
{
    m_drp.disconnect();
}

void PvaApp::_unconfigure()
{
    m_drp.unconfigure();  // TebContributor must be shut down before the worker
    m_det->shutdown();
}

json PvaApp::connectionInfo()
{
    std::string ip = getNicIp();
    logging::debug("nic ip  %s", ip.c_str());
    json body = {{"connect_info", {{"nic_ip", ip}}}};
    json info = m_det->connectionInfo();
    body["connect_info"].update(info);
    json bufInfo = m_drp.connectionInfo(ip);
    body["connect_info"].update(bufInfo);
    return body;
}

void PvaApp::_error(const std::string& which, const nlohmann::json& msg, const std::string& errorMsg)
{
    json body = json({});
    body["err_info"] = errorMsg;
    json answer = createMsg(which, msg["header"]["msg_id"], getId(), body);
    reply(answer);
}

void PvaApp::handleConnect(const nlohmann::json& msg)
{
    std::string errorMsg = m_drp.connect(msg, getId());
    if (!errorMsg.empty()) {
        logging::error("Error in DrpBase::connect");
        logging::error("%s", errorMsg.c_str());
        _error("connect", msg, errorMsg);
        return;
    }

    m_det->nodeId = m_drp.nodeId();
    m_det->connect(msg, std::to_string(getId()));

    m_unconfigure = false;

    json body = json({});
    json answer = createMsg("connect", msg["header"]["msg_id"], getId(), body);
    reply(answer);
}

void PvaApp::handleDisconnect(const json& msg)
{
    // Carry out the queued Unconfigure, if there was one
    if (m_unconfigure) {
        _unconfigure();
        m_unconfigure = false;
    }

    _disconnect();

    json body = json({});
    reply(createMsg("disconnect", msg["header"]["msg_id"], getId(), body));
}

void PvaApp::handlePhase1(const json& msg)
{
    std::string key = msg["header"]["key"];
    logging::debug("handlePhase1 for %s in PvaDetectorApp", key.c_str());

    XtcData::Xtc& xtc = m_det->transitionXtc();
    XtcData::TypeId tid(XtcData::TypeId::Parent, 0);
    xtc.src = XtcData::Src(m_det->nodeId); // set the src field for the event builders
    xtc.damage = 0;
    xtc.contains = tid;
    xtc.extent = sizeof(XtcData::Xtc);

    json phase1Info{ "" };
    if (msg.find("body") != msg.end()) {
        if (msg["body"].find("phase1Info") != msg["body"].end()) {
            phase1Info = msg["body"]["phase1Info"];
        }
    }

    json body = json({});

    if (key == "configure") {
        if (m_unconfigure) {
            _unconfigure();
            m_unconfigure = false;
        }

        std::string errorMsg = m_drp.configure(msg);
        if (!errorMsg.empty()) {
            errorMsg = "Phase 1 error: " + errorMsg;
            logging::error("%s", errorMsg.c_str());
            _error(key, msg, errorMsg);
            return;
        }

        std::string config_alias = msg["body"]["config_alias"];
        unsigned error = m_det->configure(config_alias, xtc);
        if (error) {
            std::string errorMsg = "Phase 1 error in Detector::configure";
            logging::error("%s", errorMsg.c_str());
            _error(key, msg, errorMsg);
            return;
        }

        m_drp.runInfoSupport(xtc, m_det->namesLookup());
    }
    else if (key == "unconfigure") {
        // "Queue" unconfiguration until after phase 2 has completed
        m_unconfigure = true;
    }
    else if (key == "beginrun") {
        RunInfo runInfo;
        std::string errorMsg = m_drp.beginrun(phase1Info, runInfo);
        if (!errorMsg.empty()) {
            body["err_info"] = errorMsg;
            logging::error("%s", errorMsg.c_str());
        }
        else if (runInfo.runNumber > 0) {
            m_drp.runInfoData(xtc, m_det->namesLookup(), runInfo);
        }
    }
    else if (key == "endrun") {
        std::string errorMsg = m_drp.endrun(phase1Info);
        if (!errorMsg.empty()) {
            body["err_info"] = errorMsg;
            logging::error("%s", errorMsg.c_str());
        }
    }

    json answer = createMsg(key, msg["header"]["msg_id"], getId(), body);
    reply(answer);
}

void PvaApp::handleReset(const nlohmann::json& msg)
{
    _shutdown();
    m_drp.reset();
}

} // namespace Drp


void get_kwargs(Drp::Parameters& para, const std::string& kwargs_str) {
    std::istringstream ss(kwargs_str);
    std::string kwarg;
    while (getline(ss, kwarg, ',')) {
        kwarg.erase(std::remove(kwarg.begin(), kwarg.end(), ' '), kwarg.end());
        auto pos = kwarg.find("=", 0);
        if (pos == std::string::npos) {
            logging::critical("Keyword argument with no equal sign");
            throw "error: keyword argument with no equal sign: "+kwargs_str;
        }
        std::string key = kwarg.substr(0,pos);
        std::string value = kwarg.substr(pos+1,kwarg.length());
        //cout << kwarg << " " << key << " " << value << endl;
        para.kwargs[key] = value;
    }
}

int main(int argc, char* argv[])
{
    Drp::Parameters para;
    std::string kwargs_str;
    int c;
    while((c = getopt(argc, argv, "p:o:l:D:S:C:d:u:k:P:T::M:v")) != EOF) {
        switch(c) {
            case 'p':
                para.partition = std::stoi(optarg);
                break;
            case 'o':
                para.outputDir = optarg;
                break;
            case 'l':
                para.laneMask = std::stoul(optarg, nullptr, 16);
                break;
            case 'D':
                para.detType = optarg;  // Defaults to 'pv'
                break;
            case 'S':
                para.serNo = optarg;
                break;
            case 'u':
                para.alias = optarg;
                break;
            case 'C':
                para.collectionHost = optarg;
                break;
            case 'd':
                para.device = optarg;
                break;
            case 'k':
                kwargs_str = std::string(optarg);
                break;
            case 'P':
                para.instrument = optarg;
                break;
            case 'M':
                para.prometheusDir = optarg;
                break;
            case 'v':
                ++para.verbose;
                break;
            default:
                return 1;
        }
    }

    switch (para.verbose) {
        case 0:  logging::init(para.instrument.c_str(), LOG_INFO);   break;
        default: logging::init(para.instrument.c_str(), LOG_DEBUG);  break;
    }
    logging::info("logging configured");
    if (para.instrument.empty()) {
        logging::warning("-P: instrument name is missing");
    }
    // Check required parameters
    if (para.partition == unsigned(-1)) {
        logging::critical("-p: partition is mandatory");
        return 1;
    }
    if (para.device.empty()) {
        logging::critical("-d: device is mandatory");
        return 1;
    }
    if (para.alias.empty()) {
        logging::critical("-u: alias is mandatory");
        return 1;
    }

    // Only one lane is supported by this DRP
    if (std::bitset<8>(para.laneMask).count() != 1) {
        logging::critical("-l: lane mask must have only 1 bit set");
        return 1;
    }

    // Allow detType to be overridden, but generally, psana will expect 'pv'
    if (para.detType.empty()) {
      para.detType = "pv";
    }

    // Alias must be of form <detName>_<detSegment>
    size_t found = para.alias.rfind('_');
    if ((found == std::string::npos) || !isdigit(para.alias.back())) {
        logging::critical("-u: alias must have _N suffix");
        return 1;
    }
    para.detName = para.alias.substr(0, found);
    para.detSegment = std::stoi(para.alias.substr(found+1, para.alias.size()));

    // Provider is "pva" (default) or "ca"
    std::string pvDescriptor;           // [<provider>/]<PV name>
    if (optind < argc)
        pvDescriptor = argv[optind];
    else {
        logging::critical("A PV ([<provider>/]<PV name>) is mandatory");
        return 1;
    }

    para.maxTrSize = 256 * 1024;
    para.nTrBuffers = 32; // Power of 2 greater than the maximum number of
                          // transitions in the system at any given time, e.g.,
                          // MAX_LATENCY * (SlowUpdate rate), in same units
    try {
        get_kwargs(para, kwargs_str);

        Py_Initialize(); // for use by configuration
        Drp::PvaApp app(para, pvDescriptor);
        app.run();
        app.handleReset(json({}));
        Py_Finalize(); // for use by configuration
        return 0;
    }
    catch (std::exception& e)  { logging::critical("%s", e.what()); }
    catch (std::string& e)     { logging::critical("%s", e.c_str()); }
    catch (char const* e)      { logging::critical("%s", e); }
    catch (...)                { logging::critical("Default exception"); }
    return EXIT_FAILURE;
}
