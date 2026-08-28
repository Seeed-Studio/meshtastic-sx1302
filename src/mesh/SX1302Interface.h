#pragma once

#if defined(ARCH_PORTDUINO) && defined(MESHTASTIC_USE_SX1302)

#include "RadioInterface.h"
#include "MeshPacketQueue.h"
#include "concurrency/NotifiedWorkerThread.h"
#include "platform/portduino/sx1302/SX1261Cad.h"
#include "platform/portduino/sx1302/SX1302Hal.h"

class SX1302Interface : public RadioInterface, protected concurrency::NotifiedWorkerThread
{
  public:
    SX1302Interface();
    ~SX1302Interface() override;

    bool init() override;
    bool reconfigure() override;
    bool sleep() override;
    bool canSleep() override;

    ErrorCode send(meshtastic_MeshPacket *p) override;
    meshtastic_QueueStatus getQueueStatus() override;
    bool cancelSending(NodeNum from, PacketId id) override;
    bool findInTxQueue(NodeNum from, PacketId id) override;

    bool isChannelActive();
    bool isActivelyReceiving();
    bool isIRQPending() override { return false; }
    uint32_t getPacketTime(uint32_t totalPacketLen, bool received = false) override;

  protected:
    void onNotify(uint32_t notification) override;
    int16_t getCurrentRSSI() override { return lastRssi; }

  private:
    static constexpr uint32_t POLL_INTERVAL_MS = 10;
    enum Notification { POLL_TICK = 1 };

    bool canSendImmediately();
    void handlePollTick();
    void schedulePollTick();
    void startTransmit(meshtastic_MeshPacket *packet);
    void completeSending();
    bool deliverPacket(const sx1302_bridge_rx_packet_t &packet);

    MeshPacketQueue txQueue = MeshPacketQueue(MAX_TX_QUEUE);
    SX1302Hal hal;
    SX1261Cad cad;
    bool receiving = false;
    bool active = false;
    bool txDelayActive = false;
    uint32_t txDelayStartedAt = 0;
    uint32_t txDelayMsec = 0;
    int16_t lastRssi = -120;
    uint32_t rxBad = 0, rxGood = 0, txGood = 0, txRelay = 0;
    uint16_t txDrop = 0;
};

#endif
