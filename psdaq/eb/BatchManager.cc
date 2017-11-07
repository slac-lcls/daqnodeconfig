#include "BatchManager.hh"
#include "Endpoint.hh"

#include "xtcdata/xtc/Dgram.hh"

using namespace XtcData;
using namespace Pds;
using namespace Pds::Eb;
using namespace Pds::Fabrics;

BatchManager::BatchManager(Src      src,
                           uint64_t duration, // = ~((1 << N) - 1) = 128 uS?
                           unsigned batchDepth,
                           unsigned maxEntries,
                           size_t   contribSize) :
  _src          (src),
  _duration     (duration),
  _durationShift(__builtin_ctzll(duration)),
  _durationMask (~((1 << __builtin_ctzll(duration)) - 1) & ((1UL << 56) - 1)),
  _maxBatchSize (maxEntries * contribSize),
  _pool         (Batch::size(), batchDepth),
  _inFlightList ()
{
  if (__builtin_popcountll(duration) != 1)
  {
    fprintf(stderr, "Batch duration (%016lx) must be a power of 2\n",
            duration);
    abort();
  }
}

BatchManager::~BatchManager()
{
}

void* BatchManager::batchPool() const
{
  return _pool.buffer();
}

size_t BatchManager::batchPoolSize() const
{
  return _pool.size();
}

void BatchManager::start(unsigned      batchDepth,
                         unsigned      maxEntries,
                         MemoryRegion* mr[2])
{
  printf("Dumping pool 1:\n");  _pool.dump();

  Batch::init(_pool, batchDepth, maxEntries, mr);

  printf("Dumping pool 2:\n");  _pool.dump();

  Dgram dg;
  dg.seq = Sequence(ClockTime(0, 0), TimeStamp());

  _batch = new(&_pool) Batch(_src, dg, dg.seq.stamp().pulseId());
}

void BatchManager::process(const Dgram* contrib, void* arg)
{
  uint64_t pid = _startId(contrib->seq.stamp().pulseId());

  if (_batch->expired(pid))
  {
    _inFlightList.insert(_batch);       // Revisit: Replace with atomic list

    post(_batch, arg);

    _batch = new(&_pool) Batch(_src, *contrib, pid); // Resource wait if pool is empty
  }

  _batch->append(*contrib);
}

void BatchManager::release(uint64_t id)
{
  Batch* batch = _inFlightList.atHead();
  Batch* end   = _inFlightList.empty();
  while (batch != end)
  {
    //printf("%s: id = %014lx, bid = %014lx\n", __PRETTY_FUNCTION__, id, batch->id());

    if (id < batch->id())  break;

    Entry* next = batch->next();

    delete (Batch*)_inFlightList.remove(batch); // Revisit: Replace with atomic list

    batch = (Batch*)next;
  }
}

size_t BatchManager::dstOffset(unsigned idx) const
{
  return idx * _maxBatchSize;
}

//uint64_t BatchManager::batchId(uint64_t id) const
//{
//  return id / _duration;         // Batch number since EPOCH
//}

uint64_t BatchManager::batchId(uint64_t id) const
{
  return id >> _durationShift;          // Batch number since EPOCH
}

//uint64_t BatchManager::_startId(uint64_t id) const
//{
//  return _batchId(id) * _duration;     // Current batch ID
//}

uint64_t BatchManager::_startId(uint64_t id) const
{
  return id & _durationMask;
}
