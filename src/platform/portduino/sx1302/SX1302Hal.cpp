#include "SX1302Hal.h"

#if defined(ARCH_PORTDUINO) && defined(MESHTASTIC_USE_SX1302)

#include "PortduinoGlue.h"
#include "SPILock.h"
#include "logging.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <iterator>
#include <utility>

namespace {
struct TxPowerEntry {
    int8_t rfPower;
    uint8_t paGain;
    uint8_t powerIndex;
};

// Default SX1250 values used by the upstream HAL packet-forwarder profiles.
constexpr TxPowerEntry txPowerTable[] = {
    {12, 0, 15}, {13, 0, 16}, {14, 0, 17}, {15, 0, 19}, {16, 0, 20}, {17, 0, 22},
    {18, 1, 1},  {19, 1, 2},  {20, 1, 3},  {21, 1, 4},  {22, 1, 5},  {23, 1, 6},
    {24, 1, 7},  {25, 1, 9},  {26, 1, 11}, {27, 1, 14},
};
} // namespace

SX1302Hal::SX1302Hal(std::string path, uint8_t chain, uint8_t channel, std::string type)
    : spiPath(std::move(path)), rfChain(chain), ifChain(channel), radioType(std::move(type))
{
}

SX1302Hal::~SX1302Hal()
{
    end();
}

bool SX1302Hal::configureHal(uint32_t frequencyHz, float bandwidthKHz, uint8_t spreadingFactor, uint8_t codingRate, int8_t txPower)
{
    if (rfChain >= 2 || ifChain != 8 || radioType != "sx1250")
        return false;

    sx1302_bridge_tx_lut_t txGain {};
    if (txPower < txPowerTable[0].rfPower) {
        txGain.size = 1;
        txGain.lut[0].rf_power = txPower;
        txGain.lut[0].pa_gain = 0;
        txGain.lut[0].pwr_idx = 0;
    } else {
        txGain.size = static_cast<uint8_t>(std::size(txPowerTable));
        for (uint8_t i = 0; i < txGain.size; ++i) {
            txGain.lut[i].rf_power = txPowerTable[i].rfPower;
            txGain.lut[i].pa_gain = txPowerTable[i].paGain;
            txGain.lut[i].pwr_idx = txPowerTable[i].powerIndex;
        }
    }

    const uint8_t bandwidth = bandwidthKHz >= 500 ? SX1302_BRIDGE_BW_500 : (bandwidthKHz >= 250 ? SX1302_BRIDGE_BW_250 : SX1302_BRIDGE_BW_125);
    return sx1302_bridge_configure(spiPath.c_str(), rfChain, ifChain, frequencyHz, bandwidth, spreadingFactor, codingRate, &txGain) == 0;
}

bool SX1302Hal::begin(uint32_t frequencyHz, float bandwidthKHz, uint8_t spreadingFactor, uint8_t codingRate, int8_t txPower)
{
    if (started)
        return true;
    if (!configureHal(frequencyHz, bandwidthKHz, spreadingFactor, codingRate, txPower))
        return false;

    concurrency::LockGuard guard(spiLock);
    started = sx1302_bridge_start() == 0;
    if (!started)
        LOG_ERROR("SX1302: failed to start concentrator");
    return started;
}

bool SX1302Hal::reconfigure(uint32_t frequencyHz, float bandwidthKHz, uint8_t spreadingFactor, uint8_t codingRate, int8_t txPower)
{
    if (started)
        end();
    return begin(frequencyHz, bandwidthKHz, spreadingFactor, codingRate, txPower);
}

void SX1302Hal::end()
{
    if (!started)
        return;
    concurrency::LockGuard guard(spiLock);
    sx1302_bridge_stop();
    started = false;
}

int SX1302Hal::receive(sx1302_bridge_rx_packet_t *packets, uint8_t maxPackets)
{
    if (!started)
        return -1;
    concurrency::LockGuard guard(spiLock);
    return sx1302_bridge_receive(maxPackets, packets);
}

bool SX1302Hal::send(uint32_t frequencyHz, uint8_t spreadingFactor, float bandwidthKHz, uint8_t codingRate, uint16_t preamble,
                     int8_t txPower, const uint8_t *payload, uint16_t payloadLength)
{
    if (!started || payload == nullptr || payloadLength == 0)
        return false;

    concurrency::LockGuard guard(spiLock);
    const uint8_t bandwidth = bandwidthKHz >= 500 ? SX1302_BRIDGE_BW_500 : (bandwidthKHz >= 250 ? SX1302_BRIDGE_BW_250 : SX1302_BRIDGE_BW_125);
    return sx1302_bridge_send(rfChain, frequencyHz, spreadingFactor, bandwidth, codingRate, preamble, txPower, payload,
                              payloadLength) == 0;
}

bool SX1302Hal::txFree()
{
    if (!started)
        return true;

    concurrency::LockGuard guard(spiLock);
    return sx1302_bridge_tx_free(rfChain) != 0;
}

uint32_t SX1302Hal::timeOnAir(uint8_t spreadingFactor, float bandwidthKHz, uint8_t codingRate, uint16_t preamble,
                               uint16_t payloadLength) const
{
    const uint8_t bandwidth = bandwidthKHz >= 500 ? SX1302_BRIDGE_BW_500 : (bandwidthKHz >= 250 ? SX1302_BRIDGE_BW_250 : SX1302_BRIDGE_BW_125);
    return sx1302_bridge_time_on_air(spreadingFactor, bandwidth, codingRate, preamble, payloadLength);
}

#else

SX1302Hal::SX1302Hal(std::string, uint8_t, uint8_t, std::string) {}
SX1302Hal::~SX1302Hal() = default;
bool SX1302Hal::begin(uint32_t, float, uint8_t, uint8_t, int8_t) { return false; }
bool SX1302Hal::reconfigure(uint32_t, float, uint8_t, uint8_t, int8_t) { return false; }
void SX1302Hal::end() {}
int SX1302Hal::receive(sx1302_bridge_rx_packet_t *, uint8_t) { return -1; }
bool SX1302Hal::send(uint32_t, uint8_t, float, uint8_t, uint16_t, int8_t, const uint8_t *, uint16_t) { return false; }
bool SX1302Hal::txFree() { return false; }
uint32_t SX1302Hal::timeOnAir(uint8_t, float, uint8_t, uint16_t, uint16_t) const { return 0; }

#endif
