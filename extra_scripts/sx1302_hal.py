from pathlib import Path


Import("env")

if env["PIOENV"] != "sensecap-m1":
    Return()


def write_compat_source(source, compat_dir, contents):
    compat_dir.mkdir(parents=True, exist_ok=True)
    compat_source = compat_dir / source.name
    if not compat_source.exists() or compat_source.read_text() != contents:
        compat_source.write_text(contents)


def make_loragw_sx1302_compat(source, compat_dir):
    service_private_syncword = """        DEBUG_PRINTF(\"INFO: configuring LoRa (Service) SF%u with syncword PRIVATE (0x12)\\n\", lora_service_sf);
        err |= lgw_reg_w(SX1302_REG_RX_TOP_LORA_SERVICE_FSK_FRAME_SYNCH0_PEAK1_POS, 2);
        err |= lgw_reg_w(SX1302_REG_RX_TOP_LORA_SERVICE_FSK_FRAME_SYNCH1_PEAK2_POS, 4);
"""
    tx_private_syncword = """                DEBUG_MSG(\"Setting LoRa syncword 0x12\\n\");
                err = lgw_reg_w(SX1302_REG_TX_TOP_FRAME_SYNCH_0_PEAK1_POS(pkt_data->rf_chain), 2);
                CHECK_ERR(err);
                err = lgw_reg_w(SX1302_REG_TX_TOP_FRAME_SYNCH_1_PEAK2_POS(pkt_data->rf_chain), 4);
                CHECK_ERR(err);
"""

    contents = source.read_text()
    if contents.count(service_private_syncword) != 1 or contents.count(tx_private_syncword) != 1:
        raise RuntimeError("unsupported sx1302_hal loragw_sx1302.c layout")

    contents = contents.replace(
        service_private_syncword,
        """        DEBUG_PRINTF(\"INFO: configuring LoRa (Service) SF%u with syncword Meshtastic (0x2B)\\n\", lora_service_sf);
        err |= lgw_reg_w(SX1302_REG_RX_TOP_LORA_SERVICE_FSK_FRAME_SYNCH0_PEAK1_POS, 0x2 << 1);
        err |= lgw_reg_w(SX1302_REG_RX_TOP_LORA_SERVICE_FSK_FRAME_SYNCH1_PEAK2_POS, 0xB << 1);
""",
    )
    contents = contents.replace(
        tx_private_syncword,
        """                DEBUG_MSG(\"Setting LoRa syncword Meshtastic 0x2B\\n\");
                err = lgw_reg_w(SX1302_REG_TX_TOP_FRAME_SYNCH_0_PEAK1_POS(pkt_data->rf_chain), 0x2 << 1);
                CHECK_ERR(err);
                err = lgw_reg_w(SX1302_REG_TX_TOP_FRAME_SYNCH_1_PEAK2_POS(pkt_data->rf_chain), 0xB << 1);
                CHECK_ERR(err);
""",
    )
    write_compat_source(source, compat_dir, contents)


project_dir = Path(env.subst("$PROJECT_DIR"))
hal_root = project_dir / "third_party" / "sx1302_hal"
loragw_src = hal_root / "libloragw" / "src"
libtools_src = hal_root / "libtools" / "src"
config_include = project_dir / "src" / "platform" / "portduino" / "sx1302" / "hal_config"
build_dir = Path(env.subst("$BUILD_DIR"))
loragw_compat_src = build_dir / "sx1302_hal_compat"
make_loragw_sx1302_compat(loragw_src / "loragw_sx1302.c", loragw_compat_src)

env.Append(
    CPPPATH=[
        str(config_include),
        str(hal_root / "libloragw" / "inc"),
        str(loragw_src),
        str(hal_root / "libtools" / "inc"),
    ],
    LINKFLAGS=["-lrt", "-lm"],
)

# Build the reusable HAL sources in the firmware target. The upstream Makefile
# also builds gateway applications and test programs; none belong in firmware.
env.BuildSources(
    str(Path(env.subst("$BUILD_DIR")) / "sx1302_hal" / "libloragw"),
    str(loragw_src),
    "+<*> -<loragw_sx1302.c>",
)
env.BuildSources(
    str(Path(env.subst("$BUILD_DIR")) / "sx1302_hal" / "compat"),
    str(loragw_compat_src),
)
env.BuildSources(
    str(Path(env.subst("$BUILD_DIR")) / "sx1302_hal" / "libtools"),
    str(libtools_src),
)
