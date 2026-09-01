import subprocess
from pathlib import Path


Import("env")

if env["PIOENV"] != "sensecap-m1":
    Return()


def write_compat_source(source, compat_dir, contents):
    compat_dir.mkdir(parents=True, exist_ok=True)
    compat_source = compat_dir / source.name
    if not compat_source.exists() or compat_source.read_text() != contents:
        compat_source.write_text(contents)


def apply_meshtastic_patch(compat_dir, source_name, patch_file):
    """Apply the Meshtastic HAL diff to the compat copy.

    Covers preamble size (10/8/12 -> 16 symbols, MultiSF + LoRa Service modems)
    and syncword (MultiSF + LoRa Service + TX -> 0x2B, the PRIVATE branches that
    run since the firmware sets lorawan_public=false). Kept as a unified diff so
    the upstream change stays reviewable and the build fails loudly if upstream
    drifts away from the expected context.
    """
    result = subprocess.run(
        ["patch", "-p3", "--batch", "-i", str(patch_file)],
        cwd=compat_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to apply meshtastic HAL patch {patch_file} to "
            f"{compat_dir / source_name}:\n{result.stdout}{result.stderr}"
        )


def make_loragw_sx1302_compat(source, compat_dir, meshtastic_patch):
    # The compat copy is the pristine upstream source; every Meshtastic tweak
    # (preamble size + syncword) lands through the unified diff in meshtastic_patch.
    write_compat_source(source, compat_dir, source.read_text())
    apply_meshtastic_patch(compat_dir, source.name, meshtastic_patch)


project_dir = Path(env.subst("$PROJECT_DIR"))
hal_root = project_dir / "third_party" / "sx1302_hal"
loragw_src = hal_root / "libloragw" / "src"
libtools_src = hal_root / "libtools" / "src"
config_include = project_dir / "src" / "platform" / "portduino" / "sx1302" / "hal_config"
build_dir = Path(env.subst("$BUILD_DIR"))
loragw_compat_src = build_dir / "sx1302_hal_compat"
make_loragw_sx1302_compat(
    loragw_src / "loragw_sx1302.c",
    loragw_compat_src,
    project_dir / "extra_scripts" / "sx1302_hal_meshtastic.patch",
)

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
