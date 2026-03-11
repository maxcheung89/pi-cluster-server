#!/usr/bin/env python3
"""
Raspberry Pi SSD1306 OLED Monitor (128x64 / 0.96")

Layout:
  Line 1 (fixed): IP: <ip address>
  Line 2 (fixed): <hostname>  <RAM size>
  ════════════════ divider ════════════════
  PAGE A:
    Line 3: CPU  XX%  T:XX.XC
    Line 4: Net:<iface> [UP/DOWN]
  PAGE B:
    Line 3: Disk: XX.X/XXG  XX%
    Line 4: HH:MM:SS  up Xh Ym
"""

import time
import socket
import psutil
import threading
import ipaddress
from datetime import datetime
from zoneinfo import ZoneInfo          # Python 3.9+ (on all Pi OS Bookworm/Ubuntu 22+)
from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
I2C_ADDRESS      = 0x3C
I2C_PORT         = 1
PAGE_FLIP_SEC    = 5
FRAME_SLEEP      = 0.05
NET_REFRESH_SEC  = 2
DATA_REFRESH_SEC = 3
DISPLAY_W        = 128
DISPLAY_H        = 64
LINE_HEIGHT      = 14
SCROLL_SPEED     = 1
SCROLL_GAP       = 28
TIMEZONE         = ZoneInfo("America/Chicago")   # Dallas / Central Time

Y_LINE1   = 1
Y_LINE2   = Y_LINE1 + LINE_HEIGHT
Y_DIVIDER = Y_LINE2 + LINE_HEIGHT + 1
Y_LINE3   = Y_DIVIDER + 3
Y_LINE4   = Y_LINE3 + LINE_HEIGHT

try:
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 11)
    font_bold = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 11)
except IOError:
    font = font_bold = ImageFont.load_default()


# ──────────────────────────────────────────────
# Uptime — kernel /proc/uptime only, never psutil
# ──────────────────────────────────────────────
def get_uptime_str():
    try:
        with open("/proc/uptime") as f:
            secs = int(float(f.read().split()[0]))
    except Exception:
        return "--"
    days  = secs // 86400
    hours = (secs % 86400) // 3600
    mins  = (secs % 3600) // 60
    if days  > 0:  return f"{days}d{hours}h"
    if hours > 0:  return f"{hours}h{mins}m"
    return f"{mins}m"


# ──────────────────────────────────────────────
# Dallas time — system clock in America/Chicago timezone
# ──────────────────────────────────────────────
def get_dallas_time():
    now = datetime.now(tz=TIMEZONE)
    return now.strftime("%H:%M:%S")


# ──────────────────────────────────────────────
# IP / Network helpers
#
# Blocked IP ranges (never show these as "real" IPs):
#   127.x.x.x     loopback
#   169.254.x.x   APIPA / link-local (Pi self-assigns when DHCP fails)
#   172.16-31.x.x Docker / VM bridge networks  ← NEW
#   192.168.0.x   Common Pi DHCP-fallback range
#
# Physical link check:
#   /sys/class/net/<iface>/carrier = "1" means cable plugged in
#   If that file is unreadable (wifi) we allow it through
# ──────────────────────────────────────────────

# Pre-build the Docker/VM range for fast checking
_DOCKER_RANGES = [
    ipaddress.ip_network(f"172.{n}.0.0/16") for n in range(16, 32)
]

def _is_valid_ip(ip_str):
    """Return True only if this is a real routable LAN address."""
    if ip_str.startswith("127."):      return False   # loopback
    if ip_str.startswith("169.254."):  return False   # APIPA
    if ip_str.startswith("192.168.0."): return False  # Pi fallback
    try:
        addr = ipaddress.ip_address(ip_str)
        for net in _DOCKER_RANGES:
            if addr in net:
                return False                          # Docker/VM bridge
    except ValueError:
        return False
    return True


def _has_carrier(iface):
    """Read /sys carrier file. Returns True if cable is physically plugged in."""
    try:
        with open(f"/sys/class/net/{iface}/carrier") as f:
            return f.read().strip() == "1"
    except Exception:
        return True   # wireless or unreadable — assume ok


def _iface_short(iface):
    """
    Keep the real interface name but shorten only if it would be too long.
    eth0  → eth0   (not E0)
    wlan0 → wlan0
    enp3s0 → enp3s0
    End result is readable and not squished.
    """
    return iface if len(iface) <= 6 else iface[:6]


def _scan_ifaces():
    """
    Return list of (iface_name, ip_str) for all interfaces that:
      - are not loopback
      - are marked up by OS
      - have physical carrier (for ethernet)
      - have a valid non-fallback IPv4 address
    Sorted: ethernet first, then wireless.
    """
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    eth, wifi = [], []

    for iface, addr_list in addrs.items():
        if iface == "lo":
            continue
        if iface in stats and not stats[iface].isup:
            continue
        if not _has_carrier(iface):
            continue
        for addr in addr_list:
            if addr.family != socket.AF_INET:
                continue
            if not _is_valid_ip(addr.address):
                continue
            is_eth = iface.startswith(("eth", "en", "eno", "ens", "enp"))
            (eth if is_eth else wifi).append((iface, addr.address))
            break

    return eth + wifi


def get_active_ip():
    found = _scan_ifaces()
    return found[0][1] if found else "No IP"


def get_network_str():
    found = _scan_ifaces()

    if not found:
        # No valid interface at all — don't even bother checking internet
        return "Net:None [DOWN]"

    # Build interface label — show real name(s)
    names     = " ".join(_iface_short(iface) for iface, _ in found)
    iface_str = names if names else "None"

    # Only check internet if we have a real local IP
    inet = False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect(("1.1.1.1", 53))
        s.close()
        inet = True
    except Exception:
        pass

    return f"Net:{iface_str} [{'UP' if inet else 'DOWN'}]"


# ──────────────────────────────────────────────
# CPU — dedicated sampler thread so nothing
# interferes with the internal counter
# ──────────────────────────────────────────────
_cpu_pct  = 0.0
_cpu_lock = threading.Lock()

def cpu_sampler_loop():
    global _cpu_pct
    psutil.cpu_percent(interval=1)   # warm-up call, result discarded
    while True:
        val = psutil.cpu_percent(interval=1)
        with _cpu_lock:
            _cpu_pct = val

def get_cpu_pct():
    with _cpu_lock:
        return _cpu_pct


def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000.0
    except Exception:
        for key in ("cpu_thermal", "cpu-thermal", "coretemp"):
            t = psutil.sensors_temperatures()
            if key in t and t[key]:
                return t[key][0].current
    return 0.0


def get_cpu_str():
    # Fixed-width format keeps characters from bunching:
    #   CPU: XX%  T:XX.XC
    # pct is right-aligned in 3 chars so "2%" shows as " 2%"
    return f"CPU:{get_cpu_pct():3.0f} %  T:{get_cpu_temp():.1f}°C"


def get_disk():
    u = psutil.disk_usage("/")
    return f"Disk:{u.used/1024**3:.1f}/{u.total/1024**3:.0f}G {u.percent:.0f}%"


def get_hostname():
    return socket.gethostname()

def get_ram_label():
    gb = psutil.virtual_memory().total / (1024 ** 3)
    if gb < 3:  return "2GB"
    if gb < 6:  return "4GB"
    return "8GB"


# ──────────────────────────────────────────────
# Data store
# ──────────────────────────────────────────────
class DataStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "ip":       "...",
            "hostname": get_hostname(),
            "ram":      get_ram_label(),
            "cpu":      "CPU:  -%  T:-.-C",
            "network":  "Net:-- [--]",
            "disk":     "Disk:-/-G  --%",
        }

    def update(self, key, value):
        with self._lock:
            self._data[key] = value

    def get(self, key):
        with self._lock:
            return self._data.get(key, "")

store = DataStore()


def network_refresh_loop():
    while True:
        try:
            store.update("ip",      get_active_ip())
            store.update("network", get_network_str())
        except Exception as e:
            print(f"Net refresh error: {e}")
        time.sleep(NET_REFRESH_SEC)


def slow_refresh_loop():
    while True:
        try:
            store.update("cpu",  get_cpu_str())
            store.update("disk", get_disk())
        except Exception as e:
            print(f"Slow refresh error: {e}")
        time.sleep(DATA_REFRESH_SEC)


# ──────────────────────────────────────────────
# Scroller
# ──────────────────────────────────────────────
def text_px_width(text, f):
    bbox = f.getbbox(text)
    return bbox[2] - bbox[0]

class Scroller:
    def __init__(self, max_height=LINE_HEIGHT):
        self.offset     = 0
        self._text      = ""
        self._font      = font
        self._width     = 0
        self._scrolling = False
        self._max_h     = max_height

    def set(self, text, f=None):
        if f is None:
            f = font
        if text != self._text or f != self._font:
            self._text      = text
            self._font      = f
            self._width     = text_px_width(text, f)
            self._scrolling = self._width > DISPLAY_W
            self.offset     = 0

    def reset(self):
        self.offset = 0

    def tick(self):
        if self._scrolling:
            self.offset += SCROLL_SPEED
            if self.offset >= self._width + SCROLL_GAP:
                self.offset = 0

    def draw_onto(self, image, y):
        tmp   = Image.new("1", (DISPLAY_W, self._max_h), 0)
        tdraw = ImageDraw.Draw(tmp)
        if not self._scrolling:
            tdraw.text((0, 0), self._text, font=self._font, fill=255)
        else:
            loop_w = self._width + SCROLL_GAP
            wide   = Image.new("1", (loop_w * 2, self._max_h), 0)
            wd     = ImageDraw.Draw(wide)
            wd.text((0,      0), self._text, font=self._font, fill=255)
            wd.text((loop_w, 0), self._text, font=self._font, fill=255)
            crop = wide.crop((int(self.offset), 0,
                              int(self.offset) + DISPLAY_W, self._max_h))
            tdraw.bitmap((0, 0), crop, fill=255)
        image.paste(tmp, (0, y))


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    print(f"Starting OLED monitor — I2C {hex(I2C_ADDRESS)}")
    serial = i2c(port=I2C_PORT, address=I2C_ADDRESS)
    device = ssd1306(serial)
    device.contrast(200)

    s1 = Scroller(max_height=LINE_HEIGHT)
    s2 = Scroller(max_height=LINE_HEIGHT)
    s3 = Scroller(max_height=LINE_HEIGHT)
    s4 = Scroller(max_height=LINE_HEIGHT)

    threading.Thread(target=cpu_sampler_loop,     daemon=True).start()
    threading.Thread(target=network_refresh_loop, daemon=True).start()
    threading.Thread(target=slow_refresh_loop,    daemon=True).start()

    last_page = -1

    try:
        while True:
            page = int(time.time() / PAGE_FLIP_SEC) % 2

            if page != last_page:
                s3.reset()
                s4.reset()
                last_page = page

            s1.set(f"IP:{store.get('ip')}",                        font_bold)
            s2.set(f"{store.get('hostname')}  {store.get('ram')}", font)

            if page == 0:
                s3.set(store.get("cpu"),     font)
                s4.set(store.get("network"), font)
            else:
                s3.set(store.get("disk"),    font)
                s4.set(f"{get_dallas_time()}  up:{get_uptime_str()}", font)

            image = Image.new("1", (DISPLAY_W, DISPLAY_H), 0)
            s1.draw_onto(image, Y_LINE1)
            s2.draw_onto(image, Y_LINE2)
            s3.draw_onto(image, Y_LINE3)
            s4.draw_onto(image, Y_LINE4)

            # Divider drawn LAST
            draw = ImageDraw.Draw(image)
            draw.line([(0, Y_DIVIDER), (DISPLAY_W, Y_DIVIDER)], fill=255)

            dot_y = DISPLAY_H - 5
            draw.ellipse([DISPLAY_W-13, dot_y, DISPLAY_W-9,  dot_y+4],
                         fill=255 if page == 0 else 0, outline=255)
            draw.ellipse([DISPLAY_W-7,  dot_y, DISPLAY_W-3,  dot_y+4],
                         fill=255 if page == 1 else 0, outline=255)

            device.display(image)
            s1.tick(); s2.tick(); s3.tick(); s4.tick()
            time.sleep(FRAME_SLEEP)

    except KeyboardInterrupt:
        device.clear()
        print("Display cleared. Goodbye!")

if __name__ == "__main__":
    main()
