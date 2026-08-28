#pragma once

#include <cstdint>
#include <cstddef>
#include <string>

#include "SX1302HalBridge.h"

class SX1302Hal
{
  public:
    SX1302Hal(std::string spiPath, uint8_t rfChain, uint8_t ifChain, std::string radioType);
    ~SX1302Hal();

    bool begin(uint32_t frequencyHz, float bandwidthKHz, uint8_t spreadingFactor, uint8_t codingRate, int8_t txPower);
    bool reconfigure(uint32_t frequencyHz, float bandwidthKHz, uint8_t spreadingFactor, uint8_t codingRate, int8_t txPower);
    void end();

    int receive(sx1302_bridge_rx_packet_t *packets, uint8_t maxPackets);
    bool send(uint32_t frequencyHz, uint8_t spreadingFactor, float bandwidthKHz, uint8_t codingRate, uint16_t preamble,
              int8_t txPower, const uint8_t *payload, uint16_t payloadLength);
    bool txFree();
    uint32_t timeOnAir(uint8_t spreadingFactor, float bandwidthKHz, uint8_t codingRate, uint16_t preamble,
                       uint16_t payloadLength) const;

  private:
    bool configureHal(uint32_t frequencyHz, float bandwidthKHz, uint8_t spreadingFactor, uint8_t codingRate, int8_t txPower);

    std::string spiPath;
    uint8_t rfChain;
    uint8_t ifChain;
    std::string radioType;
    bool started = false;
};
