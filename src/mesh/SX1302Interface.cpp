#include "SX1302Interface.h"

#if defined(ARCH_PORTDUINO) && defined(MESHTASTIC_USE_SX1302)

#include "NodeDB.h"
#include "PowerMon.h"
#include "PortduinoGlue.h"
#include "Throttle.h"
#include "platform/portduino/sx1302/SX1302HalBridge.h"

#include <Arduino.h>
#include <algorithm>
#include <cmath>
#include <cstring>

namespace {
uint32_t toHz(float frequencyMHz)
{
    return static_cast<uint32_t>(frequencyMHz * 1000000.0f + 0.5f);
}

uint8_t toCodingRate(uint8_t codingRate)
{
    return std::clamp<uint8_t>(codingRate, 5, 8);
}

bool resetChip(const pinMapping &pin, PinStatus first, PinStatus second, const char *name)
{
    if (!pin.enabled || pin.pin == RADIOLIB_NC) {
        LOG_ERROR("SX1302: %s reset GPIO is not configured", name);
        return false;
    }

    pinMode(pin.pin, OUTPUT);
    digitalWrite(pin.pin, first);
    delay(1);
    digitalWrite(pin.pin, second);
    delay(5);
    return true;
}

bool resetBundledRadios()
{
    return resetChip(portduino_config.sx1302_reset_pin, HIGH, LOW, "SX1302") &&
           resetChip(portduino_config.sx1261_reset_pin, LOW, HIGH, "SX1261");
}
} // namespace

SX1302Interface *SX1302Interface::instance;

SX1302Interface::SX1302Interface()
    : NotifiedWorkerThread("SX1302Radio"),
      hal(portduino_config.lora_spi_dev, portduino_config.sx1302_rf_chain, portduino_config.sx1302_if_chain,
          portduino_config.sx1302_radio),
      cad(portduino_config.sx1261_spi_dev, portduino_config.spiSpeed)
{
    instance = this;
}

SX1302Interface::~SX1302Interface()
{
    sleep();
}

bool SX1302Interface::init()
{
    if (!RadioInterface::init())
        return false;

    if (!resetBundledRadios()) {
        portduino_status.LoRa_in_error = true;
        return false;
    }

    if (!hal.begin(toHz(getFreq()), bw, sf, toCodingRate(cr), power)) {
        portduino_status.LoRa_in_error = true;
        return false;
    }

    const bool cadReady = cad.begin() && cad.configure(toHz(getFreq()), bw, sf, toCodingRate(cr));
    if (!cadReady) {
        LOG_ERROR("SX1302: SX1261 CAD initialization failed");
        portduino_status.LoRa_in_error = true;
        hal.end();
        cad.end();
        return false;
    }

    receiving = true;
    active = true;
    powerMon->setState(meshtastic_PowerMon_State_Lora_RXOn);
    schedulePollTick();
    LOG_INFO("SX1302 RF params: freq=%.3f MHz, bw=%.0f kHz, sf=%u, cr=4/%u, power=%d, preamble=%u, rf_chain=%u, if_chain=%u", getFreq(),
             bw, (unsigned)sf, (unsigned)cr, (int)power, (unsigned)preambleLength, (unsigned)portduino_config.sx1302_rf_chain,
             (unsigned)portduino_config.sx1302_if_chain);
    LOG_INFO("SX1302/SX1250 init success; IF%d active, SX1261 CAD enabled", portduino_config.sx1302_if_chain);
    return true;
}

bool SX1302Interface::reconfigure()
{
    if (!RadioInterface::reconfigure())
        return false;

    if (!resetBundledRadios()) {
        portduino_status.LoRa_in_error = true;
        return false;
    }

    const bool result = hal.reconfigure(toHz(getFreq()), bw, sf, toCodingRate(cr), power) &&
                        cad.configure(toHz(getFreq()), bw, sf, toCodingRate(cr));
    if (!result)
        portduino_status.LoRa_in_error = true;
    receiving = result;
    active = result;
    txDelayActive = false;
    if (result) {
        powerMon->setState(meshtastic_PowerMon_State_Lora_RXOn);
        schedulePollTick();
        LOG_INFO("SX1302 RF params: freq=%.3f MHz, bw=%.0f kHz, sf=%u, cr=4/%u, power=%d, preamble=%u, rf_chain=%u, if_chain=%u", getFreq(),
                 bw, (unsigned)sf, (unsigned)cr, (int)power, (unsigned)preambleLength, (unsigned)portduino_config.sx1302_rf_chain,
                 (unsigned)portduino_config.sx1302_if_chain);
    }
    return result;
}

bool SX1302Interface::sleep()
{
    receiving = false;
    active = false;
    txDelayActive = false;
    hal.end();
    cad.end();
    powerMon->clearState(meshtastic_PowerMon_State_Lora_RXOn);
    powerMon->clearState(meshtastic_PowerMon_State_Lora_TXOn);
    return true;
}

bool SX1302Interface::canSleep()
{
    return txQueue.empty() && sendingPacket == nullptr;
}

ErrorCode SX1302Interface::send(meshtastic_MeshPacket *p)
{
    if (disabled || !config.lora.tx_enabled) {
        packetPool.release(p);
        return ERRNO_DISABLED;
    }
    if (p->to == NODENUM_BROADCAST_NO_LORA)
        return ERRNO_SHOULD_RELEASE;

    bool dropped = false;
    if (!txQueue.enqueue(p, &dropped)) {
        packetPool.release(p);
        return ERRNO_UNKNOWN;
    }
    if (dropped) {
        txDrop++;
        LOG_WARN("SX1302 TX queue dropped an older packet");
    }
    notify(POLL_TICK, false);
    return ERRNO_OK;
}

meshtastic_QueueStatus SX1302Interface::getQueueStatus()
{
    meshtastic_QueueStatus status;
    status.res = status.mesh_packet_id = 0;
    status.free = txQueue.getFree();
    status.maxlen = txQueue.getMaxLen();
    return status;
}

bool SX1302Interface::cancelSending(NodeNum from, PacketId id)
{
    auto packet = txQueue.remove(from, id);
    if (packet)
        packetPool.release(packet);
    return packet != nullptr;
}

bool SX1302Interface::findInTxQueue(NodeNum from, PacketId id)
{
    return txQueue.find(from, id);
}

bool SX1302Interface::canSendImmediately()
{
    return sendingPacket == nullptr && hal.txFree();
}

void SX1302Interface::startTransmit(meshtastic_MeshPacket *packet)
{
    if (!packet)
        return;

    const size_t length = beginSending(packet);
    powerMon->setState(meshtastic_PowerMon_State_Lora_TXOn);
    const bool sent = hal.send(toHz(getFreq()), sf, bw, toCodingRate(cr), preambleLength, power, reinterpret_cast<uint8_t *>(&radioBuffer),
                               static_cast<uint16_t>(length));
    if (!sent) {
        LOG_ERROR("SX1302: transmit failed");
        portduino_status.LoRa_in_error = true;
        completeSending();
        receiving = true;
        powerMon->clearState(meshtastic_PowerMon_State_Lora_TXOn);
        powerMon->setState(meshtastic_PowerMon_State_Lora_RXOn);
        return;
    }

    receiving = false;
    powerMon->clearState(meshtastic_PowerMon_State_Lora_RXOn);
    txDelayActive = false;
}

void SX1302Interface::completeSending()
{
    if (!sendingPacket)
        return;

    auto packet = sendingPacket;
    sendingPacket = nullptr;
    txGood++;
    if (!isFromUs(packet))
        txRelay++;
    airTime->logAirtime(TX_LOG, RadioInterface::getPacketTime(packet));
    packetPool.release(packet);
    powerMon->clearState(meshtastic_PowerMon_State_Lora_TXOn);
    receiving = true;
    powerMon->setState(meshtastic_PowerMon_State_Lora_RXOn);
}

bool SX1302Interface::deliverPacket(const sx1302_bridge_rx_packet_t &packet)
{
    if (packet.size < sizeof(PacketHeader) || packet.size > sizeof(radioBuffer)) {
        LOG_WARN("SX1302 RX drop: bad size=%u (hdr=%u, buf=%u)", (unsigned)packet.size, (unsigned)sizeof(PacketHeader),
                 (unsigned)sizeof(radioBuffer));
        rxBad++;
        return false;
    }
    if (packet.status == SX1302_BRIDGE_STAT_CRC_BAD) {
        LOG_WARN("SX1302 RX drop: CRC bad, size=%u", (unsigned)packet.size);
        rxBad++;
        return false;
    }

    memcpy(&radioBuffer, packet.payload, packet.size);
    const auto payloadLength = packet.size - sizeof(PacketHeader);
    if (radioBuffer.header.from == 0 || payloadLength > sizeof(radioBuffer.payload)) {
        LOG_WARN("SX1302 RX drop: from=0x%x or payload %u > %u", (unsigned)radioBuffer.header.from, (unsigned)payloadLength,
                 (unsigned)sizeof(radioBuffer.payload));
        rxBad++;
        return false;
    }

    auto *meshPacket = packetPool.allocZeroed();
    meshPacket->from = radioBuffer.header.from;
    meshPacket->to = radioBuffer.header.to;
    meshPacket->id = radioBuffer.header.id;
    meshPacket->channel = radioBuffer.header.channel;
    meshPacket->hop_limit = radioBuffer.header.flags & PACKET_FLAGS_HOP_LIMIT_MASK;
    meshPacket->hop_start = (radioBuffer.header.flags & PACKET_FLAGS_HOP_START_MASK) >> PACKET_FLAGS_HOP_START_SHIFT;
    meshPacket->want_ack = !!(radioBuffer.header.flags & PACKET_FLAGS_WANT_ACK_MASK);
    meshPacket->via_mqtt = !!(radioBuffer.header.flags & PACKET_FLAGS_VIA_MQTT_MASK);
    meshPacket->next_hop = meshPacket->hop_start == 0 ? NO_NEXT_HOP_PREFERENCE : radioBuffer.header.next_hop;
    meshPacket->relay_node = meshPacket->hop_start == 0 ? NO_RELAY_NODE : radioBuffer.header.relay_node;
    meshPacket->rx_snr = packet.snr;
    meshPacket->rx_rssi = lround(packet.rssis);
    meshPacket->which_payload_variant = meshtastic_MeshPacket_encrypted_tag;
    meshPacket->encrypted.size = payloadLength;
    memcpy(meshPacket->encrypted.bytes, radioBuffer.payload, payloadLength);

    rxGood++;
    lastRssi = meshPacket->rx_rssi;
    LOG_INFO("SX1302 RX ok: from=0x%x, to=0x%x, id=0x%x, ch=%u, rssi=%d, snr=%.1f, size=%u, payload=%u",
             (unsigned)radioBuffer.header.from, (unsigned)radioBuffer.header.to, (unsigned)radioBuffer.header.id,
             (unsigned)radioBuffer.header.channel, (int)meshPacket->rx_rssi, (double)packet.snr, (unsigned)packet.size,
             (unsigned)payloadLength);
#ifdef LED_LORA
    loraRxPacketObservable.notifyObservers(meshPacket->from);
#endif
    airTime->logAirtime(RX_LOG, getPacketTime(packet.size, true));
    deliverToReceiver(meshPacket);
    return true;
}

void SX1302Interface::schedulePollTick()
{
    notifyLater(POLL_INTERVAL_MS, POLL_TICK, false);
}

void SX1302Interface::handlePollTick()
{
    if (!active)
        return;

    static uint32_t heartbeat = 0;
    if (++heartbeat % 500 == 0)
        LOG_INFO("SX1302 poll tick: receiving=%d, sending=%d, freq=%.3f, sf=%u, bw=%.0f, cr=4/%u", (int)receiving,
                 (int)(sendingPacket != nullptr), getFreq(), (unsigned)sf, bw, (unsigned)cr);

    if (sendingPacket != nullptr) {
        if (!hal.txFree()) {
            schedulePollTick();
            return;
        }
        completeSending();
    }

    if (receiving) {
        sx1302_bridge_rx_packet_t packets[4] {};
        const int count = hal.receive(packets, 4);
        if (count < 0) {
            LOG_ERROR("SX1302 RX: hal.receive returned %d", count);
            portduino_status.LoRa_in_error = true;
        } else {
            if (count > 0)
                LOG_INFO("SX1302 RX: fetched %d packet(s) from concentrator", count);
            for (int i = 0; i < count; ++i)
                deliverPacket(packets[i]);
        }
    }
    if (txQueue.empty()) {
        txDelayActive = false;
    } else {
        if (!txDelayActive) {
            txDelayStartedAt = millis();
            txDelayMsec = getTxDelayMsec();
            txDelayActive = true;
        }

        if (!Throttle::isWithinTimespanMs(txDelayStartedAt, txDelayMsec)) {
            txDelayActive = false;
            if (canSendImmediately()) {
                if (isChannelActive()) {
                    txDelayStartedAt = millis();
                    txDelayMsec = getTxDelayMsec();
                    txDelayActive = true;
                } else {
                    startTransmit(txQueue.dequeue());
                }
            }
        }
    }

    schedulePollTick();
}

bool SX1302Interface::isChannelActive()
{
    if (!cad.configure(toHz(getFreq()), bw, sf, toCodingRate(cr)))
        return true;

    bool detected = true;
    if (!cad.scan(detected)) {
        LOG_ERROR("SX1261: CAD scan failed; treating channel as busy");
        portduino_status.LoRa_in_error = true;
        return true;
    }
    return detected;
}

bool SX1302Interface::isActivelyReceiving()
{
    // For Seeed WM1302 module, CAD is performed on the SX1261, which is a separate chip from the SX1302.
    // We can be actively receiving on the SX1302 while the SX1261 is performing CAD.
    // So isActivelyReceiving() will be unnecessary for this configuration.
    return false;
}

uint32_t SX1302Interface::getPacketTime(uint32_t totalPacketLen, bool)
{
    return hal.timeOnAir(sf, bw, toCodingRate(cr), preambleLength, totalPacketLen);
}

void SX1302Interface::onNotify(uint32_t notification)
{
    switch (notification) {
    case POLL_TICK:
        handlePollTick();
        break;
    default:
        break;
    }
}

#endif
