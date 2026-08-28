#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    SX1302_BRIDGE_BW_125 = 0,
    SX1302_BRIDGE_BW_250 = 1,
    SX1302_BRIDGE_BW_500 = 2,
};

enum {
    SX1302_BRIDGE_STAT_CRC_BAD = 0x11,
};

typedef struct {
    int8_t rf_power;
    uint8_t pa_gain;
    uint8_t pwr_idx;
} sx1302_bridge_tx_gain_t;

typedef struct {
    uint8_t size;
    sx1302_bridge_tx_gain_t lut[16];
} sx1302_bridge_tx_lut_t;

typedef struct {
    uint8_t status;
    float rssis;
    float snr;
    uint16_t size;
    uint8_t payload[256];
} sx1302_bridge_rx_packet_t;

int sx1302_bridge_configure(const char *spi_path, uint8_t rf_chain, uint8_t if_chain, uint32_t frequency_hz,
                            uint8_t bandwidth, uint8_t spreading_factor, uint8_t coding_rate,
                            const sx1302_bridge_tx_lut_t *tx_lut);
int sx1302_bridge_start(void);
int sx1302_bridge_stop(void);
int sx1302_bridge_receive(uint8_t max_packets, sx1302_bridge_rx_packet_t *packets);
int sx1302_bridge_send(uint8_t rf_chain, uint32_t frequency_hz, uint8_t spreading_factor, uint8_t bandwidth,
                       uint8_t coding_rate, uint16_t preamble, int8_t tx_power, const uint8_t *payload,
                       uint16_t payload_length);
int sx1302_bridge_tx_free(uint8_t rf_chain);
uint32_t sx1302_bridge_time_on_air(uint8_t spreading_factor, uint8_t bandwidth, uint8_t coding_rate, uint16_t preamble,
                                   uint16_t payload_length);

#ifdef __cplusplus
}
#endif
