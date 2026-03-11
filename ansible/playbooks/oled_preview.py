#!/usr/bin/env python3
"""
OLED Display Preview Tool
Simulates the SSD1306 128x64 display in your terminal AND saves a PNG.

Usage:
    python3 oled_preview.py             # show both pages, loop every 5s
    python3 oled_preview.py --page 0    # show page A only
    python3 oled_preview.py --page 1    # show page B only
    python3 oled_preview.py --once      # print both pages once and exit
    python3 oled_preview.py --png       # save preview.png and exit

No OLED hardware required. Reads real system info from this machine.
"""

import argparse
import time
import socket
import os
import sys
import psutil
import ipaddress
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    PIL_OK = True
except ImportError:
    PIL_OK = False

# ── Match exact settings from oled_monitor.py ─────────────────────
DISPLAY_W     = 128
DISPLAY_H     = 64
LINE_HEIGHT   = 14
TIMEZONE      = ZoneInfo("America/Chicago")
PAGE_FLIP_SEC = 5

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
except Exception:
    font = font_bold = ImageFont.load_default()

# ── System info (copied from oled_monitor.py) ─────────────────────
def get_uptime_str():
    try:
        with open("/proc/uptime") as f:
            secs = int(float(f.read().split()[0]))
    except Exception:
        secs = 0
    days  = secs // 86400
    hours = (secs % 86400) // 3600
    mins  = (secs % 3600) // 60
    if days  > 0: return f"{days}d{hours}h"
    if hours > 0: return f"{hours}h{mins}m"
    return f"{mins}m"

def get_dallas_time():
    return datetime.now(tz=TIMEZONE).strftime("%H:%M:%S")

_DOCKER_RANGES = [ipaddress.ip_network(f"172.{n}.0.0/16") for n in range(16,32)]

def _is_valid_ip(ip):
    if ip.startswith("127."):       return False
    if ip.startswith("169.254."):   return False
    if ip.startswith("192.168.0."): return False
    try:
        addr = ipaddress.ip_address(ip)
        if any(addr in n for n in _DOCKER_RANGES): return False
    except ValueError:
        return False
    return True

def _has_carrier(iface):
    try:
        with open(f"/sys/class/net/{iface}/carrier") as f:
            return f.read().strip() == "1"
    except Exception:
        return True

def _iface_short(iface):
    return iface if len(iface) <= 6 else iface[:6]

def _scan_ifaces():
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    eth, wifi = [], []
    for iface, addr_list in addrs.items():
        if iface == "lo": continue
        if iface in stats and not stats[iface].isup: continue
        if not _has_carrier(iface): continue
        for addr in addr_list:
            if addr.family != socket.AF_INET: continue
            if not _is_valid_ip(addr.address): continue
            is_eth = iface.startswith(("eth","en","eno","ens","enp"))
            (eth if is_eth else wifi).append((iface, addr.address))
            break
    return eth + wifi

def get_active_ip():
    found = _scan_ifaces()
    return found[0][1] if found else "No IP"

def get_network_str():
    found = _scan_ifaces()
    if not found:
        return "Net:None [DOWN]"
    names = " ".join(_iface_short(i) for i,_ in found)
    inet = False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect(("1.1.1.1", 53))
        s.close()
        inet = True
    except Exception:
        pass
    return f"Net:{names} [{'UP' if inet else 'DOWN'}]"

def get_cpu_str():
    pct  = psutil.cpu_percent(interval=0.5)
    temp = 0.0
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp = int(f.read()) / 1000.0
    except Exception:
        pass
    return f"CPU:{pct:3.0f}%  T:{temp:.1f}C"

def get_disk():
    u = psutil.disk_usage("/")
    return f"Disk:{u.used/1024**3:.1f}/{u.total/1024**3:.0f}G {u.percent:.0f}%"

def get_hostname():
    return socket.gethostname()

def get_ram_label():
    gb = psutil.virtual_memory().total / (1024**3)
    if gb < 3:  return "2GB"
    if gb < 6:  return "4GB"
    return "8GB"

# ── Build one frame as a PIL Image ────────────────────────────────
def build_frame(page):
    ip       = get_active_ip()
    hostname = get_hostname()
    ram      = get_ram_label()
    cpu      = get_cpu_str()
    network  = get_network_str()
    disk     = get_disk()
    now_str  = get_dallas_time()
    uptime   = get_uptime_str()

    lines = [
        (f"IP:{ip}",               font_bold),
        (f"{hostname}  {ram}",     font),
        (cpu    if page==0 else disk,                font),
        (network if page==0 else f"{now_str}  up:{uptime}", font),
    ]
    ys = [Y_LINE1, Y_LINE2, Y_LINE3, Y_LINE4]

    image = Image.new("1", (DISPLAY_W, DISPLAY_H), 0)
    draw  = ImageDraw.Draw(image)

    for (text, f), y in zip(lines, ys):
        # Clip each line to its lane height
        tmp = Image.new("1", (DISPLAY_W, LINE_HEIGHT), 0)
        ImageDraw.Draw(tmp).text((0, 0), text, font=f, fill=255)
        image.paste(tmp, (0, y))

    # Divider last
    draw.line([(0, Y_DIVIDER), (DISPLAY_W, Y_DIVIDER)], fill=255)

    # Page dots
    dot_y = DISPLAY_H - 5
    draw.ellipse([DISPLAY_W-13, dot_y, DISPLAY_W-9,  dot_y+4],
                 fill=255 if page==0 else 0, outline=255)
    draw.ellipse([DISPLAY_W-7,  dot_y, DISPLAY_W-3,  dot_y+4],
                 fill=255 if page==1 else 0, outline=255)

    return image, lines

# ── Terminal renderer ──────────────────────────────────────────────
BLOCK_FULL  = "█"
BLOCK_EMPTY = " "

def image_to_terminal(img):
    """
    Render the 128x64 monochrome image as ASCII blocks in the terminal.
    Each pixel = one character. Scale up 2x horizontally to compensate
    for terminal character aspect ratio.
    """
    width, height = img.width, img.height
    scale_x = 2   # characters are taller than wide, so stretch horizontally

    # Top border
    border = "┌" + "─" * (width * scale_x) + "┐"
    print(border)

    pixels = img.load()
    for y in range(height):
        row = "│"
        for x in range(width):
            ch = BLOCK_FULL if pixels[x, y] else BLOCK_EMPTY
            row += ch * scale_x
        row += "│"
        print(row)

    print("└" + "─" * (width * scale_x) + "┘")

def print_page_header(page, lines):
    page_name = "── PAGE A: CPU + Network ──" if page == 0 else "── PAGE B: Disk + Time ──"
    print(f"\n  {page_name}")
    print()

def print_page_data(lines):
    labels = ["Line 1 (IP)  ", "Line 2 (Host)", "Line 3       ", "Line 4       "]
    for label, (text, _) in zip(labels, lines):
        print(f"  {label}: {text}")
    print()

# ── PNG export ─────────────────────────────────────────────────────
PNG_SCALE = 4   # scale up so the PNG is readable at 512x256

def save_png(filename="preview.png"):
    images = []
    for page in [0, 1]:
        img, _ = build_frame(page)
        # Scale up and add a small gap between pages
        scaled = img.resize((DISPLAY_W * PNG_SCALE, DISPLAY_H * PNG_SCALE),
                             Image.NEAREST)
        # Invert so it looks like white-on-black OLED
        images.append(ImageOps.invert(scaled.convert("L")).convert("1"))

    # Stack both pages side by side with a 4px gap
    gap   = Image.new("1", (4 * PNG_SCALE, DISPLAY_H * PNG_SCALE), 0)
    total_w = DISPLAY_W * PNG_SCALE * 2 + gap.width
    combined = Image.new("L", (total_w, DISPLAY_H * PNG_SCALE), 40)  # dark bg

    # Paste page A and B as white-on-black
    img_a = build_frame(0)[0].resize((DISPLAY_W*PNG_SCALE, DISPLAY_H*PNG_SCALE), Image.NEAREST).convert("L")
    img_b = build_frame(1)[0].resize((DISPLAY_W*PNG_SCALE, DISPLAY_H*PNG_SCALE), Image.NEAREST).convert("L")

    combined.paste(img_a, (0, 0))
    combined.paste(img_b, (DISPLAY_W * PNG_SCALE + gap.width, 0))

    combined.save(filename)
    print(f"Saved: {filename}  ({combined.width}x{combined.height}px, both pages side by side)")

# ── Main ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="OLED display preview")
    parser.add_argument("--page",  type=int, choices=[0,1], help="Show only this page (0=A, 1=B)")
    parser.add_argument("--once",  action="store_true",     help="Print once and exit")
    parser.add_argument("--png",   action="store_true",     help="Save preview.png and exit")
    args = parser.parse_args()

    if not PIL_OK:
        print("ERROR: Pillow not installed.  pip3 install Pillow")
        sys.exit(1)

    # ── PNG mode ──────────────────────────────────────────────────
    if args.png:
        save_png("preview.png")
        return

    # ── Single-page or both pages once ────────────────────────────
    pages = [args.page] if args.page is not None else [0, 1]

    if args.once or args.page is not None:
        for page in pages:
            img, lines = build_frame(page)
            print_page_header(page, lines)
            image_to_terminal(img)
            print_page_data(lines)
        return

    # ── Live loop — flip every PAGE_FLIP_SEC like the real display ─
    print("Live preview — Ctrl+C to stop\n")
    try:
        while True:
            page = int(time.time() / PAGE_FLIP_SEC) % 2
            img, lines = build_frame(page)
            # Clear terminal
            os.system("clear" if os.name == "posix" else "cls")
            print(f"  OLED Preview  (synced page flip every {PAGE_FLIP_SEC}s)\n")
            print_page_header(page, lines)
            image_to_terminal(img)
            print_page_data(lines)
            print("  [Ctrl+C to stop]")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()
