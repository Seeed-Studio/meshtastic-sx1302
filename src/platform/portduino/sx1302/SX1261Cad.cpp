#include "SX1261Cad.h"

#if defined(ARCH_PORTDUINO) && defined(MESHTASTIC_USE_SX1302)

#include "SPILock.h"
#include "PortduinoGlue.h"
#include "logging.h"

#include <Arduino.h>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <fcntl.h>
#include <linux/spi/spidev.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {
constexpr uint8_t SET_STANDBY = 0x80;
constexpr uint8_t SET_PACKET_TYPE = 0x8A;
constexpr uint8_t SET_RF_FREQUENCY = 0x86;
constexpr uint8_t SET_MODULATION_PARAMS = 0x8B;
constexpr uint8_t SET_PACKET_PARAMS = 0x8C;
constexpr uint8_t SET_DIO_IRQ_PARAMS = 0x08;
constexpr uint8_t CLEAR_IRQ_STATUS = 0x02;
constexpr uint8_t GET_IRQ_STATUS = 0x12;
constexpr uint8_t SET_CAD_PARAMS = 0x88;
constexpr uint8_t SET_CAD = 0xC5;

constexpr uint16_t IRQ_CAD_DETECTED = 1U << 8;
constexpr uint16_t IRQ_CAD_DONE = 1U << 7;

uint32_t frequencyRegister(uint32_t frequencyHz)
{
    return static_cast<uint32_t>((static_cast<uint64_t>(frequencyHz) << 25) / 32000000U);
}

uint8_t bandwidthRegister(float bandwidthKHz)
{
    if (bandwidthKHz >= 500)
        return 0x06;
    if (bandwidthKHz >= 250)
        return 0x05;
    return 0x04;
}
} // namespace

SX1261Cad::SX1261Cad(std::string path, uint32_t speed) : spiPath(std::move(path)), spiSpeed(speed) {}

SX1261Cad::~SX1261Cad()
{
    end();
}

bool SX1261Cad::begin()
{
    if (fd >= 0)
        return true;

    fd = open(spiPath.c_str(), O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        LOG_ERROR("SX1261: failed to open %s", spiPath.c_str());
        return false;
    }

    uint8_t mode = SPI_MODE_0;
    uint8_t bits = 8;
    if (ioctl(fd, SPI_IOC_WR_MODE, &mode) < 0 || ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0 ||
        ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, &spiSpeed) < 0) {
        LOG_ERROR("SX1261: failed to configure SPI on %s", spiPath.c_str());
        end();
        return false;
    }

    return setStandby();
}

void SX1261Cad::end()
{
    if (fd >= 0) {
        close(fd);
        fd = -1;
    }
}

bool SX1261Cad::writeCommand(uint8_t opcode, const uint8_t *data, size_t length)
{
    if (fd < 0 || (length != 0 && data == nullptr))
        return false;

    usleep(1000);
    std::vector<uint8_t> tx(length + 1);
    tx[0] = opcode;
    if (length != 0)
        memcpy(tx.data() + 1, data, length);

    struct spi_ioc_transfer transfer {};
    transfer.tx_buf = reinterpret_cast<unsigned long>(tx.data());
    transfer.len = tx.size();
    transfer.speed_hz = spiSpeed;
    transfer.bits_per_word = 8;
    const bool success = ioctl(fd, SPI_IOC_MESSAGE(1), &transfer) == static_cast<int>(tx.size());
    usleep(1000);
    return success;
}

bool SX1261Cad::readCommand(uint8_t opcode, uint8_t *data, size_t length)
{
    if (fd < 0 || (length != 0 && data == nullptr))
        return false;

    usleep(1000);
    std::vector<uint8_t> tx(length + 2, 0);
    std::vector<uint8_t> rx(length + 2, 0);
    tx[0] = opcode;

    struct spi_ioc_transfer transfer {};
    transfer.tx_buf = reinterpret_cast<unsigned long>(tx.data());
    transfer.rx_buf = reinterpret_cast<unsigned long>(rx.data());
    transfer.len = tx.size();
    transfer.speed_hz = spiSpeed;
    transfer.bits_per_word = 8;
    if (ioctl(fd, SPI_IOC_MESSAGE(1), &transfer) != static_cast<int>(tx.size()))
        return false;

    // SX126x returns a command status byte before the command payload.
    if (length != 0)
        memcpy(data, rx.data() + 2, length);
    usleep(1000);
    return true;
}

bool SX1261Cad::setStandby()
{
    const uint8_t mode = 0;
    return writeCommand(SET_STANDBY, &mode, 1);
}

bool SX1261Cad::configure(uint32_t frequency, float bandwidth, uint8_t sf, uint8_t cr)
{
    if (fd < 0 || sf < 5 || sf > 12 || cr < 5 || cr > 8 ||
        (bandwidth != 125 && bandwidth != 250 && bandwidth != 500))
        return false;

    frequencyHz = frequency;
    bandwidthKHz = bandwidth;
    spreadingFactor = sf;

    const uint8_t packetType = 0x01;
    if (!setStandby() || !writeCommand(SET_PACKET_TYPE, &packetType, 1))
        return false;

    const uint32_t frf = frequencyRegister(frequencyHz);
    const uint8_t frequencyData[] = {static_cast<uint8_t>(frf >> 24), static_cast<uint8_t>(frf >> 16),
                                     static_cast<uint8_t>(frf >> 8), static_cast<uint8_t>(frf)};
    if (!writeCommand(SET_RF_FREQUENCY, frequencyData, sizeof(frequencyData)))
        return false;

    const bool lowDataRate = ((1U << spreadingFactor) / (bandwidthKHz * 1000.0f)) >= 0.016f;
    const uint8_t modulationData[] = {spreadingFactor, bandwidthRegister(bandwidthKHz),
                                      static_cast<uint8_t>(cr - 4), lowDataRate ? 0x01 : 0x00};
    if (!writeCommand(SET_MODULATION_PARAMS, modulationData, sizeof(modulationData)))
        return false;

    const uint8_t packetData[] = {0x00, 0x10, 0x00, 0xFF, 0x01, 0x00};
    if (!writeCommand(SET_PACKET_PARAMS, packetData, sizeof(packetData)))
        return false;

    const uint8_t irqData[] = {0x01, 0x80, 0x01, 0x80, 0x00, 0x00, 0x00, 0x00};
    return writeCommand(SET_DIO_IRQ_PARAMS, irqData, sizeof(irqData));
}

bool SX1261Cad::waitCadDone(uint16_t &irqStatus)
{
    constexpr uint32_t timeoutMs = 100;
    const uint32_t start = millis();
    uint8_t irqData[2] = {};
    while (millis() - start < timeoutMs) {
        if (!readCommand(GET_IRQ_STATUS, irqData, sizeof(irqData)))
            return false;
        irqStatus = static_cast<uint16_t>(irqData[0] << 8 | irqData[1]);
        if (irqStatus & IRQ_CAD_DONE)
            return true;
        usleep(1000);
    }
    return false;
}

bool SX1261Cad::scan(bool &detected)
{
    detected = false;
    if (fd < 0)
        return false;

    concurrency::LockGuard guard(spiLock);

    const uint8_t clearIrq[] = {0xFF, 0xFF};
    const uint8_t cadParams[] = {0x01, 0x16, 0x0A, 0x00, 0x00, 0x00, 0x00};
    if (!writeCommand(CLEAR_IRQ_STATUS, clearIrq, sizeof(clearIrq)) ||
        !writeCommand(SET_CAD_PARAMS, cadParams, sizeof(cadParams)) || !writeCommand(SET_CAD, nullptr, 0))
        return false;

    uint16_t irqStatus = 0;
    if (!waitCadDone(irqStatus))
        return false;

    detected = (irqStatus & IRQ_CAD_DETECTED) != 0;
    writeCommand(CLEAR_IRQ_STATUS, clearIrq, sizeof(clearIrq));
    return setStandby();
}

#else

SX1261Cad::SX1261Cad(std::string, uint32_t) {}
SX1261Cad::~SX1261Cad() = default;
bool SX1261Cad::begin() { return false; }
void SX1261Cad::end() {}
bool SX1261Cad::configure(uint32_t, float, uint8_t, uint8_t) { return false; }
bool SX1261Cad::scan(bool &) { return false; }

#endif
