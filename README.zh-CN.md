# SenseCAP M1 Meshtastic 网关

<p align="center">
  <img src="media/m1-gateway.png" width="700" alt="运行 Meshtastic 网关固件的 SenseCAP M1" />
</p>

<p align="center">
  <a href="https://github.com/Seeed-Studio/meshtastic-sx1302/releases">
    <img alt="GitHub release" src="https://img.shields.io/github/v/release/Seeed-Studio/meshtastic-sx1302" />
  </a>
  <a href="https://github.com/Seeed-Studio/meshtastic-sx1302/blob/sx130x_lbt_port/LICENSE">
    <img alt="许可证" src="https://img.shields.io/github/license/Seeed-Studio/meshtastic-sx1302" />
  </a>
  <a href="https://github.com/Seeed-Studio/meshtastic-sx1302/commits">
    <img alt="最近提交" src="https://img.shields.io/github/last-commit/Seeed-Studio/meshtastic-sx1302.svg" />
  </a>
  <img alt="Meshtastic" src="https://img.shields.io/badge/Meshtastic-2.7.x-blue" />
  <img alt="平台" src="https://img.shields.io/badge/平台-Raspberry%20Pi%20%C2%B7%20Debian%2013-c51a4a" />
</p>

<!-- LANG_SWITCHER_START -->
<p align="center">
  <a href="README.md">English</a> | <b>中文</b> | <a href="README.ja.md">日本語</a> | <a href="README.fr.md">Français</a> | <a href="README.pt.md">Português</a> | <a href="README.es.md">Español</a>
</p>
<!-- LANG_SWITCHER_END -->

**meshtastic-sx1302** 是面向 SX1302 LoRa 集中器硬件的 [Meshtastic](https://meshtastic.org) 固件移植。首要目标设备是 Seeed SenseCAP M1——一台以 Helium 矿机身份出厂的"树莓派 CM4 + WM1302"组合——本固件将其改造为功能完整、全天候在线的 Meshtastic 网格网关，并支持基于 SX1261 的 LBT（先听后说）。

<p align="center">
  <img src="media/architecture.jpg" alt="Architecture: Meshtastic clients connect over TCP/HTTPS to meshtasticd on SenseCAP M1, which drives the SX1302 8-channel concentrator and SX1261 LBT over SPI plus the STTS751 temperature sensor over I2C, bridging IP to the LoRa mesh" width="100%" />
</p>

[Meshtastic 文档][docs] · [快速开始](#快速开始) · [硬件要求](#硬件要求) · [提交 Bug][issues]

## 目录

- [为什么让 M1 重获新生](#为什么让-m1-重获新生)
- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [使用场景](#使用场景)
- [推荐硬件](#推荐硬件)
- [硬件要求](#硬件要求)
- [硬件自检](#硬件自检)
- [安装](#安装)
- [使用](#使用)
- [散热风扇](#散热风扇)
- [LoRa 区域与合规](#lora-区域与合规)
- [故障排查](#故障排查)
- [已知问题](#已知问题)
- [常见问题](#常见问题)
- [参与贡献](#参与贡献)

## 为什么让 M1 重获新生

2021 年的 Helium 热潮把成千上万台做工精良的小电脑送进了千家万户。挖矿收益退潮之后，硬件并没有贬值：

| 每台 SenseCAP M1 的内部配置 | |
| --- | --- |
| 计算 | 树莓派 CM4（Pi 4 级，4 GB） |
| 无线 | WM1302 模组——Semtech SX1302 集中器（8 信道）+ SX1261 |
| 传感 | STTS751 温度传感器 |
| 散热 | 金属外壳、高增益天线、温控风扇（GPIO 13） |

本仓库用 [Meshtastic](https://meshtastic.org)——开源的离网 LoRa 网格网络——替换整套挖矿软件栈，并在上游固件基础上扩展了 SX1302 集中器支持与基于 SX1261 的 LBT。一次重刷之后，这台曾经挖币的盒子开始为社区网格 7×24 小时接力消息。

> [!IMPORTANT]
> 网关模组必须是**包含 SX126x 的 WM1302 版本**（即支持 LBT 的版本）。不支持 LBT 的模组无法发挥本固件的核心能力，安装前请先运行[硬件自检](#硬件自检)确认。

## 功能特性

- **集中器级无线性能** —— 驱动 SX1302 的 8 路并行解调信道，同时聆听多个节点，而非手持电台那样的单信道
- **LBT 支持（需 SX126x）** —— 通过 SX1261 实现先听后说的信道检测，帮助满足强制要求 LBT 的地区的合规规定——这是本移植的核心增强
- **常驻网关服务** —— 内置 HTTPS Web UI（`:9443`）与 TCP API（`:4403`），服务浏览器、手机 App 与 CLI
- **一键脚本安装** —— `install.sh` 部署二进制、配置、systemd 服务与运行库，并开启自启动
- **硬件自检** —— 附带的探测脚本数秒内验证 SX1261 / SX1302 / STTS751

## 快速开始

**前置条件：** 一台 SenseCAP M1（或配了含 SX126x 的 WM1302 模组的树莓派）、一张 16 GB 以上 microSD 卡、一台带读卡器的电脑。

```bash
# 1. 用 Raspberry Pi Imager 烧录 Raspberry Pi OS Lite 64 位（Debian 13 trixie），
#    并在烧录设置中预配置 SSH 与 WiFi
#    https://www.raspberrypi.com/documentation/computers/getting-started.html

# 2. 启用 SPI/I2C，然后验证无线硬件
sudo raspi-config nonint do_spi 0 && sudo raspi-config nonint do_i2c 0
sudo apt update && sudo apt install -y python3-spidev python3-smbus gpiod  # 探测脚本依赖
python3 tools/probe_sx130x.py --reset        # 期望 3 项 PASS

# 3. 安装并启动
tar xzf meshtasticd-sensecap-m1-aarch64*.tar.gz
cd meshtasticd-sensecap-m1-aarch64*/
sudo ./install.sh
```

三步走完，矿机变网关——浏览器打开 `https://<树莓派IP>:9443` 即可访问 Web UI。

## 使用场景

- **社区网格骨干** —— 固定位置、常年在线、天线优良，把城市尺度的 Meshtastic 网络延伸到手持设备够不着的地方
- **应急备灾** —— 蜂窝与互联网瘫痪时依然可用的离网消息枢纽
- **远程监测** —— 汇集农场、园区、工地上的追踪器与传感器位置和遥测数据
- **户外无网活动** —— 徒步、越野、航海编队在信号覆盖之外保持联络
- **Meshtastic 开发** —— 一台带集中器无线电的完整 Linux 主机，是协议与应用开发的理想试验台

## 推荐硬件

刷好固件的 M1 就是网关——以下任一 Seeed Meshtastic 节点都能与之搭配：

| 设备 | 最适合 | 链接 |
| --- | --- | --- |
| SenseCAP Card Tracker T1000-E | 口袋级 GPS 追踪器，IP65 防护，开箱即用 | [购买][hw-sensecap] |
| Wio Tracker L2 Pro | 带 3.2 英寸触摸屏的独立节点——无需手机即可收发消息、查看离线地图 | [购买][hw-l2] |
| Wio Tracker L1 Pro | 带 OLED 屏的便携现场节点 | [购买][hw-wio] |
| SenseCAP MeshTracker X1 | 紧凑型 GPS 网格追踪器 | [购买][hw-x1] |
| SenseCAP Solar Node P1 / P1-Pro | 太阳能供电的户外节点——把网格延伸到没有市电的地方 | [P1][hw-solar] · [P1 Pro][hw-solar-pro] |
| XIAO + Wio-SX1262 套件 | 自制节点——可选 ESP32S3 或 nRF52840 | [ESP32S3][hw-xiao] · [nRF52840][hw-nrf] |
| Meshtastic Starter Kit | 开箱即用套装，快速组建你的第一个网格 | [购买][hw-starter] |

> **用自己的树莓派搭建** —— 手头已有 WM1302（SPI）模组？它搭载同款 SX1302 + SX1261 芯片，通过 SPI 连接树莓派。可从 [WM1302 Wiki][wiki-wm1302] 了解接线，再用同一个探测脚本验证。

> **补全你的网格** —— Seeed [Meshtastic 产品专区][hw-collection] 中的所有设备均可与本网关搭配。

## 硬件要求

| 要求 | 说明 |
| --- | --- |
| 操作系统 | Raspberry Pi OS Lite 64 位，**Debian 13 trixie** —— [安装指南][pi-getting-started] |
| 网络 | WiFi 或以太网，已配置且局域网内可达 |
| SPI / I2C | 已启用 —— [配置指南][pi-config] |
| 无线模组 | **含 SX126x 的 WM1302**（支持 LBT 的版本） |

> [!IMPORTANT]
> WM1302 的版本很重要：只有包含 SX126x 的模组才具备本固件依赖的先听后说检测能力。安装前请先运行[硬件自检](#硬件自检)确认。

## 硬件自检

```bash
# 安装依赖
sudo apt update
sudo apt install python3 python3-spidev python3-smbus gpiod i2c-tools wget git

# 授予用户访问 SPI/I2C/GPIO 设备的权限
sudo usermod -aG spi,i2c,gpio $USER

# 注销重新登录（或重启）使组变更生效，然后运行：
python3 tools/probe_sx130x.py --reset
```

三项测试全部显示 `PASS` 即为就绪：

```text
SX1261 @ /dev/spidev0.1: PASS
  pram version: SX1261 V2D 2D02
  ...
SX1302 @ /dev/spidev0.0: PASS
  version: 0x10, version string: v1.0
  ...
STTS751 @ /dev/i2c-1 address 0x39: PASS
  product: STTS751-0, temperature: 34.75 °C
  ...
Result: PASS (SX1302 + SX1261 + STTS751 all responded)
```

## 安装

### 方式 A —— 预编译版本（推荐）

从 [Releases][releases] 下载最新安装包，在设备上直接安装：

```bash
wget https://github.com/Seeed-Studio/meshtastic-sx1302/releases/latest/download/meshtasticd-sensecap-m1-aarch64.tar.gz
tar xzf meshtasticd-sensecap-m1-aarch64*.tar.gz
cd meshtasticd-sensecap-m1-aarch64*/
sudo ./install.sh
```

注意事项：

- 解压目录名带有 git 提交哈希后缀（如 `-e3a6d9dcf`），所以 `cd` 使用了通配符。
- `install.sh` 会将二进制复制到 `/usr/bin`、配置安装到 `/etc/meshtasticd/`、注册 `meshtasticd` systemd 服务、安装运行库并开启自启动。

### 方式 B —— Docker 源码编译

```bash
# Aarch64 QEMU 模拟（仅 x86 主机需要）
sudo docker run --privileged --rm tonistiigi/binfmt --install arm64

# 编译
sudo docker buildx build --platform linux/arm64 -f Dockerfile.sensecap-m1 -t meshtastic-sensecap-m1:arm64 .

# 打包
sudo docker run --rm -v "$PWD/release:/out" meshtastic-sensecap-m1:arm64 \
  sh -c 'cp /opt/firmware/.pio/build/sensecap-m1/meshtasticd /out/meshtasticd_linux_aarch64'
WEB_VERSION=2.7.2 bash bin/package-sensecap-m1.sh
```

打包好的 `meshtasticd-sensecap-m1-aarch64.tar.gz` 会保存在源码的 `release/` 目录。`WEB_VERSION` 指定打包进固件的 Meshtastic Web UI 版本——请保持在 2.7.x 系列（任一 2.7.x 标签皆可），重新打包时再按需更新。复制到树莓派后解压并执行 `install.sh`（同方式 A）。

## 使用

### Web UI

浏览器访问 `https://<树莓派IP>:9443` 即可使用 Meshtastic Web UI。首次运行需要在页面内添加连接，连接地址填写同一个 URL。HTTPS 证书为自签名——接受一次浏览器警告即可。

<p align="center">
  <img src="media/webui.png" width="700" alt="Meshtastic Web UI —— 首次运行的连接配置界面" />
</p>

> [!NOTE]
> 已知上游 Bug：Web UI 中消息的 ACK 状态可能无法正常显示，待 Meshtastic 上游修复。

### 手机 App / CLI

兼容所有 Meshtastic 客户端——Android/iOS App、CLI 或 Python SDK。连接方式选择 **TCP**，输入树莓派 IP，端口默认 **4403**：

```bash
pip install meshtastic
meshtastic --host <树莓派IP> --info
```

### 服务管理

| 操作 | 命令 |
| --- | --- |
| 查看状态 | `systemctl status meshtasticd` |
| 启动 / 停止 / 重启 | `sudo systemctl start\|stop\|restart meshtasticd` |
| 开机自启 | 默认已开启——用 `systemctl is-enabled meshtasticd` 验证 |
| 实时日志 | `journalctl -u meshtasticd -f` |

## 散热风扇

SenseCAP M1 出厂配有温控风扇，控制引脚为 **GPIO 13**。使用官方 `gpio-fan` overlay 启用——在 `/boot/firmware/config.txt` 末尾追加并重启：

```bash
dtoverlay=gpio-fan,gpiopin=13,temp=55000,hyst=5000
```

风扇 55 °C 启动、50 °C 停止。详见[树莓派官方 case-fan 文档][pi-case-fan]。

## LoRa 区域与合规

固件默认区域为 **US915**（902–928 MHz）。请根据当地法规与网格内其他节点修改——通过 Web UI 的无线设置，或 `/etc/meshtasticd/config.yaml` 的 `[Lora]` 段，改完重启服务。

| 区域 | 频段 |
| --- | --- |
| `US915`（默认） | 902–928 MHz |
| `EU_868` | 863–870 MHz |
| `CN_470` | 470–510 MHz |
| `JP923` | 920–928 MHz |

> [!IMPORTANT]
> 同一网格内所有节点的区域与调制预设必须一致。在所在区域法规之外发射可能违法——本固件的 SX126x LBT 能力正是为满足此类规则而存在。

## 故障排查

| 症状 | 检查 |
| --- | --- |
| 开机绿灯始终不闪 | SD 卡未插到位——断电后重插，听到"咔哒"声 |
| Pi 在线但电脑连不上 | 办公/公共 WiFi 常按 AP 隔离客户端——重连到同一 AP，或用 `arp -a` 定位 Pi |
| SSH 报 `Permission denied (publickey,password)` | 重刷系统后的预期现象——先用密码登录一次，再重装公钥 |
| 服务启动失败：`cannot open shared object file` | 缺运行库——`ldd /usr/bin/meshtasticd`，安装标记 `not found` 的包 |
| 脚本报 `$'\r': command not found` | Windows CRLF 换行符——`sed -i 's/\r$//' 文件名` |
| `./install.sh: Permission denied` | 传输中丢失可执行位——`chmod +x install.sh` |
| `systemctl` 找不到单元 `meshtastcd` | 拼写错误（旧文档常见）——服务名是 `meshtasticd` |
| 探测任一项 `FAIL` | SPI/I2C 启用了吗？模组插紧了吗？`usermod` 后重新登录了吗？ |

发现 Bug？请[提交 issue][issues]，附上服务状态、`journalctl -u meshtasticd` 输出与探测结果。

## 已知问题

- Web UI：消息 ACK 状态可能显示异常——Meshtastic 上游问题
- Release 包目录名包含 git 提交哈希后缀
- 固件默认区域为 US915——在其他区域投入正式使用前请修改

## 常见问题

**支持哪些 WM1302 版本？**
仅限**包含 SX126x**（支持 LBT）的模组。运行[硬件自检](#硬件自检)即可确认。

**必须要 SenseCAP M1 吗？**
M1 是开箱即用的路径，安装包也以它为目标。进阶用户可以把构建适配到其他 SX1302 主机——探测脚本是很好的起点。

**装了还能继续挖 Helium 吗？**
不能——本固件完整替换了挖矿软件栈。把它当作通往一张更有用的网络的单程票。

## 参与贡献

欢迎各种形式的贡献！

- **Bug 反馈与功能请求** —— [提交 issue][issues]
- **代码贡献** —— fork 后开分支提交 PR，风格尽量与 Meshtastic 上游保持一致
- **文档与翻译** —— 欢迎改进文档、补充新语言

---

如果这个项目让你的矿机重获新生，欢迎点个 Star ⭐ ——帮助更多人发现它！

<!-- 链接定义 -->
[docs]: https://meshtastic.org/docs/
[issues]: https://github.com/Seeed-Studio/meshtastic-sx1302/issues
[releases]: https://github.com/Seeed-Studio/meshtastic-sx1302/releases
[hw-l2]: https://www.seeedstudio.com/Wio-Tracker-L2-Pro-for-Meshtastic-p-0000.html
[hw-x1]: https://www.seeedstudio.com/sensecap-meshtracker-x1-meshtastic-gps-tracker-p-6935.html
[hw-solar]: https://www.seeedstudio.com/SenseCAP-Solar-Node-P1-for-Meshtastic-LoRa-p-6425.html
[hw-solar-pro]: https://www.seeedstudio.com/SenseCAP-Solar-Node-P1-Pro-for-Meshtastic-LoRa-p-6412.html
[hw-nrf]: https://www.seeedstudio.com/XIAO-nRF52840-Wio-SX1262-Kit-for-Meshtastic-p-6400.html
[hw-starter]: https://www.seeedstudio.com/Meshtastic-Starter-Kit-Ready-to-Use-Off-Grid-Instant-Reliable-Communication.html
[hw-collection]: https://www.seeedstudio.com/meshtastic-products
[hw-sensecap]: https://www.seeedstudio.com/SenseCAP-Card-Tracker-T1000-E-for-Meshtastic-p-5913.html
[hw-wio]: https://www.seeedstudio.com/Wio-Tracker-L1-Pro-p-6454.html
[hw-xiao]: https://www.seeedstudio.com/Wio-SX1262-with-XIAO-ESP32S3-p-5982.html
[wiki-wm1302]: https://wiki.seeedstudio.com/WM1302_module/
[pi-getting-started]: https://www.raspberrypi.com/documentation/computers/getting-started.html
[pi-config]: https://www.raspberrypi.com/documentation/computers/configuration.html
[pi-case-fan]: https://www.raspberrypi.com/documentation/computers/configuration.html?#case-fan
