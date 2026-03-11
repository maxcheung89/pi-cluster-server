# OLED System Monitor — Raspberry Pi Cluster

A system monitor for 0.96" SSD1306 OLED displays (128×64px) deployed across
5 Raspberry Pi 4B units via Ansible. All Pis show the same layout and flip
pages in sync using wall-clock time — no coordination needed.

---

## Display Layout

```
┌──────────────────────────────┐
│ IP:192.168.1.175             │  ← Line 1 — fixed, bold, scrolls if long
│ slot4_ubuntu_server_4gb  4GB │  ← Line 2 — fixed, scrolls if long
│══════════════════════════════│  ← divider line (always visible)
│ CPU:  2 %  T:42.8°C          │  ← Line 3 — flips every 5 seconds
│ Net:eth0 [UP]            ●○  │  ← Line 4 — flips every 5 seconds + page dots
└──────────────────────────────┘

Page A (first 5s)                    Page B (next 5s)
  Line 3: CPU:  X %  T:XX.X°C          Line 3: Disk:X.X/XXG  XX%
  Line 4: Net:<iface> [UP/DOWN]         Line 4: HH:MM:SS  up:Xh Ym
```

- Lines 1 and 2 are **always fixed** — they never flip
- Lines 3 and 4 **flip every 5 seconds**, synced across all Pis via wall clock
- Any line too wide for the screen **scrolls automatically**
- Page indicator dots `●○` / `○●` bottom-right show which page is active
- Time displayed in **Dallas / Central Time** (CST/CDT auto)
- Uptime read from `/proc/uptime` — always accurate after reboot

---

## Hardware

### OLED Display
| Spec | Value |
|------|-------|
| Size | 0.96 inch |
| Resolution | 128 × 64 pixels |
| Driver | SSD1306 |
| Interface | I2C |
| I2C Address | `0x3C` (default) or `0x3D` |

### Wiring (same on every Pi)
```
OLED Pin   →   Raspberry Pi Header
─────────────────────────────────
VCC        →   Pin 1  (3.3V)
GND        →   Pin 6  (GND)
SCL        →   Pin 5  (GPIO 3 / I2C SCL)
SDA        →   Pin 3  (GPIO 2 / I2C SDA)
```

---

## Project Structure

```
~/ansible/
├── inventory/
│   └── hosts.ini                  ← Pi inventory (IPs, user, sudo config)
└── playbooks/
    ├── deploy_oled.yml            ← Main deploy + screen on/off playbook
    ├── reboot_pis.yml             ← Reboot all Pis playbook
    ├── oled_monitor.py            ← Python display script (deployed to each Pi)
    └── oled_preview.py            ← Local preview tool (no OLED hardware needed)
```

---

## Inventory

`inventory/hosts.ini`:
```ini
[pis]
slot4_ubuntu_server_4gb  ansible_host=192.168.1.175
slot3_kali_pi_8gb        ansible_host=192.168.1.176
slot5_ubuntu_server_2gb  ansible_host=192.168.1.177
slot1_ubuntu_desktop_8gb ansible_host=192.168.1.178
slot2_ubuntu_server_8gb  ansible_host=192.168.1.179

[pis:vars]
ansible_user=super90
ansible_become=true
ansible_become_method=sudo
ansible_python_interpreter=/usr/bin/python3
```

---

## First-Time Setup

### 1. Install Ansible on your control machine
```bash
pip3 install ansible
```

### 2. Verify SSH access to all Pis
```bash
ansible -i inventory/hosts.ini pis -m ping
```
All 5 should return `pong`. If any fail, check SSH keys or passwords.

### 3. Full install (packages + I2C + NTP + service)
```bash
ansible-playbook -i inventory/hosts.ini playbooks/deploy_oled.yml
```

This will:
- Install system packages (`i2c-tools`, `fonts-dejavu-core`, `python3-pip`, etc.)
- Enable I2C in `/boot/firmware/config.txt`
- Load the `i2c-dev` kernel module and add `super90` to the `i2c` group
- Set timezone to `America/Chicago` (Dallas/Central) on all Pis
- Enable NTP sync via `systemd-timesyncd` so all clocks stay in sync
- Install Python libraries (`luma.oled`, `psutil`, `Pillow`, `smbus2`)
- Copy `oled_monitor.py` to `/opt/oled_monitor/` on each Pi
- Create and start a `systemd` service that runs on boot

> **Note:** If I2C was not previously enabled, reboot all Pis once after the
> first deploy:
> ```bash
> ansible-playbook -i inventory/hosts.ini playbooks/reboot_pis.yml
> ```

---

## Playbook Tags — deploy_oled.yml

| Tag | What it does |
|-----|-------------|
| *(no tag)* | Full install — packages, I2C, NTP, script, service |
| `--tags install` | Packages + I2C + NTP only (no script copy) |
| `--tags deploy` | Copy updated script + restart service |
| `--tags off` | Stop service + power off OLED screen completely |
| `--tags on` | Start service + screen resumes displaying |

```bash
# Full install
ansible-playbook -i inventory/hosts.ini playbooks/deploy_oled.yml

# Update script only (fastest — use after editing oled_monitor.py)
ansible-playbook -i inventory/hosts.ini playbooks/deploy_oled.yml --tags deploy

# Turn off all screens (display goes completely dark)
ansible-playbook -i inventory/hosts.ini playbooks/deploy_oled.yml --tags off

# Turn screens back on
ansible-playbook -i inventory/hosts.ini playbooks/deploy_oled.yml --tags on

# Target a single Pi by slot name
ansible-playbook -i inventory/hosts.ini playbooks/deploy_oled.yml --tags deploy --limit slot1_ubuntu_desktop_8gb
ansible-playbook -i inventory/hosts.ini playbooks/deploy_oled.yml --tags off    --limit slot3_kali_pi_8gb
ansible-playbook -i inventory/hosts.ini playbooks/deploy_oled.yml --tags on     --limit slot5_ubuntu_server_2gb
```

---

## Reboot Playbook — reboot_pis.yml

Reboots all Pis and waits for each one to come back online before continuing.

```bash
# Reboot all 5 Pis
ansible-playbook -i inventory/hosts.ini playbooks/reboot_pis.yml

# Reboot a single Pi
ansible-playbook -i inventory/hosts.ini playbooks/reboot_pis.yml --limit slot2_ubuntu_server_8gb
ansible-playbook -i inventory/hosts.ini playbooks/reboot_pis.yml --limit slot4_ubuntu_server_4gb
```

Ansible automatically waits up to 2 minutes per Pi for SSH to return, then
confirms the system is healthy before marking it done.

---

## Preview Tool — oled_preview.py

Test what the display will look like before deploying. Runs on any Pi over SSH
with no OLED hardware needed. Uses real live system data from that machine.

```bash
# Copy to a Pi
scp playbooks/oled_preview.py super90@192.168.1.175:~

# SSH in and run
ssh super90@192.168.1.175

# Live preview — flips pages in sync with the real display
python3 oled_preview.py

# Show just Page A (CPU + Network)
python3 oled_preview.py --page 0

# Show just Page B (Disk + Time)
python3 oled_preview.py --page 1

# Print both pages once and exit
python3 oled_preview.py --once

# Save a PNG image of both pages side by side
python3 oled_preview.py --png
```

---

## Quick Reference — All Hosts

| Slot | Hostname | IP | RAM |
|------|----------|----|-----|
| slot1 | slot1_ubuntu_desktop_8gb | 192.168.1.178 | 8GB |
| slot2 | slot2_ubuntu_server_8gb  | 192.168.1.179 | 8GB |
| slot3 | slot3_kali_pi_8gb        | 192.168.1.176 | 8GB |
| slot4 | slot4_ubuntu_server_4gb  | 192.168.1.175 | 4GB |
| slot5 | slot5_ubuntu_server_2gb  | 192.168.1.177 | 2GB |

---

## Script Configuration

Edit these values at the top of `oled_monitor.py` before deploying:

| Variable | Default | Description |
|----------|---------|-------------|
| `I2C_ADDRESS` | `0x3C` | I2C address — confirm with `i2cdetect -y 1` |
| `I2C_PORT` | `1` | I2C bus (1 for all Pi 2/3/4/5) |
| `PAGE_FLIP_SEC` | `5` | Seconds per page — all Pis sync via wall clock |
| `FRAME_SLEEP` | `0.05` | Render loop delay (~20fps) |
| `SCROLL_SPEED` | `1` | Pixels per frame for scrolling text |
| `SCROLL_GAP` | `28` | Pixel gap between scroll loop restarts |
| `NET_REFRESH_SEC` | `2` | How often IP + internet status is checked |
| `DATA_REFRESH_SEC` | `3` | How often CPU + disk is checked |
| `TIMEZONE` | `America/Chicago` | Timezone for Page B clock |

---

## IP Address Logic

The following addresses are always rejected — display shows `No IP`:

| Range | Reason |
|-------|--------|
| `127.x.x.x` | Loopback |
| `169.254.x.x` | APIPA — Pi self-assigned when DHCP fails |
| `192.168.0.x` | Common Pi DHCP fallback range |
| `172.16–31.x.x` | Docker / VM bridge networks |

Internet `[UP/DOWN]` is only checked if a valid IP is found first. No valid IP
means immediate `[DOWN]` with no network check attempted.

---

## Troubleshooting

### Display is blank after deploy
```bash
sudo i2cdetect -y 1
```
You will see `3c` or `3d`. Update `I2C_ADDRESS` in `oled_monitor.py` and redeploy:
```bash
ansible-playbook -i inventory/hosts.ini playbooks/deploy_oled.yml --tags deploy
```

### Screen not turning off with `--tags off`
The off task sends raw I2C bytes (`0x8D`, `0x10`, `0xAE`) via `smbus2` to cut
the charge pump and power down the panel. Ensure `smbus2` is installed:
```bash
ansible -i inventory/hosts.ini pis -m shell \
  -a "pip3 install smbus2 --break-system-packages" --become
```

### "No module named luma" error in logs
```bash
ansible -i inventory/hosts.ini pis -m shell \
  -a "pip3 install luma.oled --break-system-packages" --become
```

### I2C device not found (`/dev/i2c-1` missing)
```bash
ansible-playbook -i inventory/hosts.ini playbooks/reboot_pis.yml
```

### Uptime showing wrong value after reboot
Ensure you are running the latest script — it reads `/proc/uptime` directly:
```bash
ansible-playbook -i inventory/hosts.ini playbooks/deploy_oled.yml --tags deploy
```

### One Pi out of sync on page flips
Check NTP status across all Pis:
```bash
ansible -i inventory/hosts.ini pis -m shell -a "timedatectl status" --become
```

### Check service logs on all Pis
```bash
ansible -i inventory/hosts.ini pis -m shell \
  -a "journalctl -u oled-monitor -n 20 --no-pager" --become
```

### Restart service on all Pis
```bash
ansible -i inventory/hosts.ini pis -m shell \
  -a "systemctl restart oled-monitor" --become
```

---

## How Page Sync Works

Every Pi independently calculates the current page using:

```python
page = int(time.time() / PAGE_FLIP_SEC) % 2
```

`time.time()` returns UTC epoch seconds. All Pis share the same UTC reference
via NTP so they flip at the exact same second with zero coordination. The
playbook enforces NTP sync on all Pis during install.

---

## Files Deployed to Each Pi

| Path | Description |
|------|-------------|
| `/opt/oled_monitor/oled_monitor.py` | The display script |
| `/etc/systemd/system/oled-monitor.service` | Systemd service unit (auto-starts on boot) |
