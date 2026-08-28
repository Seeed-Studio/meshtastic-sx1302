#include "SX1302HalBridge.h"

#include "loragw_hal.h"

#include <string.h>

static uint8_t bridge_bandwidth(uint8_t bandwidth)
{
    switch (bandwidth) {
    case SX1302_BRIDGE_BW_500:
        return BW_500KHZ;
    case SX1302_BRIDGE_BW_250:
        return BW_250KHZ;
    default:
        return BW_125KHZ;
    }
}

static uint8_t bridge_coding_rate(uint8_t coding_rate)
{
    if (coding_rate < 5)
        return CR_LORA_4_5;
    if (coding_rate > 8)
        return CR_LORA_4_8;
    return (uint8_t)(coding_rate - 4);
}

int sx1302_bridge_configure(const char *spi_path, uint8_t rf_chain, uint8_t if_chain, uint32_t frequency_hz,
                            uint8_t bandwidth, uint8_t spreading_factor, uint8_t coding_rate,
                            const sx1302_bridge_tx_lut_t *tx_lut)
{
    struct lgw_conf_board_s board;
    struct lgw_conf_rxrf_s rf;
    struct lgw_conf_rxif_s if_config;
    struct lgw_conf_demod_s demod;
    struct lgw_tx_gain_lut_s tx_gain;

    if (spi_path == NULL || tx_lut == NULL || rf_chain >= LGW_RF_CHAIN_NB || if_chain >= LGW_IF_CHAIN_NB)
        return LGW_HAL_ERROR;

    memset(&board, 0, sizeof(board));
    board.lorawan_public = false;
    board.clksrc = rf_chain;
    board.full_duplex = false;
    board.com_type = LGW_COM_SPI;
    strncpy(board.com_path, spi_path, sizeof(board.com_path) - 1);

    memset(&rf, 0, sizeof(rf));
    rf.enable = true;
    rf.freq_hz = frequency_hz;
    rf.rssi_offset = -215.4f;
    rf.type = LGW_RADIO_TYPE_SX1250;
    rf.tx_enable = true;
    rf.single_input_mode = true;

    memset(&if_config, 0, sizeof(if_config));
    if_config.enable = true;
    if_config.rf_chain = rf_chain;
    if_config.bandwidth = bridge_bandwidth(bandwidth);
    if_config.datarate = spreading_factor;
    if_config.implicit_hdr = false;
    if_config.implicit_crc_en = true;
    if_config.implicit_coderate = bridge_coding_rate(coding_rate);

    memset(&demod, 0, sizeof(demod));

    memset(&tx_gain, 0, sizeof(tx_gain));
    tx_gain.size = tx_lut->size > TX_GAIN_LUT_SIZE_MAX ? TX_GAIN_LUT_SIZE_MAX : tx_lut->size;
    for (uint8_t i = 0; i < tx_gain.size; ++i) {
        tx_gain.lut[i].rf_power = tx_lut->lut[i].rf_power;
        tx_gain.lut[i].dig_gain = 0;
        tx_gain.lut[i].pa_gain = tx_lut->lut[i].pa_gain;
        tx_gain.lut[i].dac_gain = 0;
        tx_gain.lut[i].mix_gain = 8;
        tx_gain.lut[i].offset_i = 0;
        tx_gain.lut[i].offset_q = 0;
        tx_gain.lut[i].pwr_idx = tx_lut->lut[i].pwr_idx;
    }

    if (lgw_board_setconf(&board) != LGW_HAL_SUCCESS || lgw_rxrf_setconf(rf_chain, &rf) != LGW_HAL_SUCCESS ||
        lgw_rxif_setconf(if_chain, &if_config) != LGW_HAL_SUCCESS || lgw_demod_setconf(&demod) != LGW_HAL_SUCCESS ||
        lgw_txgain_setconf(rf_chain, &tx_gain) != LGW_HAL_SUCCESS)
        return LGW_HAL_ERROR;

    return LGW_HAL_SUCCESS;
}

int sx1302_bridge_start(void)
{
    return lgw_start();
}

int sx1302_bridge_stop(void)
{
    return lgw_stop();
}

int sx1302_bridge_receive(uint8_t max_packets, sx1302_bridge_rx_packet_t *packets)
{
    struct lgw_pkt_rx_s vendor_packets[8];
    int count;

    if (packets == NULL || max_packets == 0)
        return LGW_HAL_ERROR;
    if (max_packets > 8)
        max_packets = 8;

    memset(vendor_packets, 0, sizeof(vendor_packets));
    count = lgw_receive(max_packets, vendor_packets);
    if (count < 0)
        return count;

    for (int i = 0; i < count; ++i) {
        packets[i].status = vendor_packets[i].status;
        packets[i].rssis = vendor_packets[i].rssis;
        packets[i].snr = vendor_packets[i].snr;
        packets[i].size = vendor_packets[i].size > sizeof(packets[i].payload) ? sizeof(packets[i].payload) : vendor_packets[i].size;
        memcpy(packets[i].payload, vendor_packets[i].payload, packets[i].size);
    }
    return count;
}

int sx1302_bridge_send(uint8_t rf_chain, uint32_t frequency_hz, uint8_t spreading_factor, uint8_t bandwidth,
                       uint8_t coding_rate, uint16_t preamble, int8_t tx_power, const uint8_t *payload,
                       uint16_t payload_length)
{
    struct lgw_pkt_tx_s packet;

    if (payload == NULL || payload_length == 0 || payload_length > sizeof(packet.payload))
        return LGW_HAL_ERROR;

    memset(&packet, 0, sizeof(packet));
    packet.freq_hz = frequency_hz;
    packet.tx_mode = IMMEDIATE;
    packet.rf_chain = rf_chain;
    packet.rf_power = tx_power;
    packet.modulation = MOD_LORA;
    packet.bandwidth = bridge_bandwidth(bandwidth);
    packet.datarate = spreading_factor;
    packet.coderate = bridge_coding_rate(coding_rate);
    packet.preamble = preamble;
    packet.size = payload_length;
    memcpy(packet.payload, payload, payload_length);
    return lgw_send(&packet);
}

int sx1302_bridge_tx_free(uint8_t rf_chain)
{
    uint8_t status = TX_STATUS_UNKNOWN;
    return lgw_status(rf_chain, TX_STATUS, &status) == LGW_HAL_SUCCESS && status == TX_FREE;
}

uint32_t sx1302_bridge_time_on_air(uint8_t spreading_factor, uint8_t bandwidth, uint8_t coding_rate, uint16_t preamble,
                                   uint16_t payload_length)
{
    struct lgw_pkt_tx_s packet;

    memset(&packet, 0, sizeof(packet));
    packet.modulation = MOD_LORA;
    packet.bandwidth = bridge_bandwidth(bandwidth);
    packet.datarate = spreading_factor;
    packet.coderate = bridge_coding_rate(coding_rate);
    packet.preamble = preamble;
    packet.size = payload_length;
    return lgw_time_on_air(&packet);
}
