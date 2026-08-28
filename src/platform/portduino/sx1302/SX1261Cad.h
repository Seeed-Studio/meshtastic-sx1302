#pragma once

#include <cstdint>
#include <string>
#include <cstddef>

class SX1261Cad
{
  public:
    explicit SX1261Cad(std::string spiPath, uint32_t spiSpeed = 2000000);
    ~SX1261Cad();

    bool begin();
    void end();
    bool configure(uint32_t frequencyHz, float bandwidthKHz, uint8_t spreadingFactor, uint8_t codingRate);
    bool scan(bool &detected);

  private:
    bool writeCommand(uint8_t opcode, const uint8_t *data, size_t length);
    bool readCommand(uint8_t opcode, uint8_t *data, size_t length);
    bool setStandby();
    bool waitCadDone(uint16_t &irqStatus);

    std::string spiPath;
    uint32_t spiSpeed;
    int fd = -1;
    uint32_t frequencyHz = 0;
    float bandwidthKHz = 125;
    uint8_t spreadingFactor = 9;
};
