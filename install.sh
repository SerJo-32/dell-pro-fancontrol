#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Dell Pro Fan Control (dellfan) installer — patched dell-smm-hwmon driver (DKMS) + fan service + desktop app.
#
#   sudo ./install.sh              driver + service + desktop app
#   sudo ./install.sh --no-gui     driver + service (server without a desktop)
#   sudo ./install.sh --no-driver  service + app only (your kernel already supports the fan)
#   sudo ./install.sh --force      skip the model check (other Dell models — at your own risk)
#
# Tested on Ubuntu 24.04 (kernels 6.17 OEM and 7.0 HWE) on a Dell Pro Micro Plus QBM1250.
set -euo pipefail

PKG=dell-smm-hwmon-qbm1250
PKG_VER=1.1
OLD_PKG_VERS=(1.0)          # removed on upgrade
# Kernel series prepared besides the running and the installed ones: Ubuntu 24.04 moves from the
# 6.17 OEM kernel to the 7.0 HWE kernel, so both are always ready. Series whose kernel already
# knows the model (Linux 7.5+) are skipped automatically.
EXTRA_SERIES=(6.17 7.0)
PREFIX=/opt/dellfan
SUPPORTED_MODELS=("Dell Pro Micro Plus QBM1250")
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WITH_GUI=1 WITH_DRIVER=1 FORCE=0
for a in "$@"; do
  case "$a" in
    --no-gui) WITH_GUI=0 ;;
    --no-driver) WITH_DRIVER=0 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '4,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $a" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!!  %s\033[0m\n' "$*"; }
die()  { printf '\033[31mxx  %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run with sudo: sudo ./install.sh"
command -v apt-get >/dev/null || die "Only Debian/Ubuntu (apt) is supported by this installer for now."

# ---------------------------------------------------------------- 1. checks
say "Checking the computer"
VENDOR=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || true)
MODEL=$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)
BIOS=$(cat /sys/class/dmi/id/bios_version 2>/dev/null || true)
echo "    $VENDOR / $MODEL / BIOS $BIOS / kernel $(uname -r)"
[ "$VENDOR" = "Dell Inc." ] || die "This is not a Dell computer."
supported=0
for m in "${SUPPORTED_MODELS[@]}"; do [ "$MODEL" = "$m" ] && supported=1; done
if [ $supported -eq 0 ]; then
  if [ $FORCE -eq 1 ]; then
    warn "Model '$MODEL' is not tested. The driver patch only knows the models listed in README."
  else
    die "Model '$MODEL' is not tested. Read README → 'Other Dell models' and re-run with --force if you understand the risk."
  fi
fi

fan_hwmon() {   # prints the dell_smm hwmon directory, if any
  for h in /sys/class/hwmon/hwmon*; do
    [ "$(cat "$h/name" 2>/dev/null)" = dell_smm ] && { echo "$h"; return 0; }
  done
  return 1
}

# ---------------------------------------------------------------- 2. packages
say "Installing packages"
pkgs=(python3)
[ $WITH_DRIVER -eq 1 ] && pkgs+=(dkms build-essential curl patch mokutil "linux-headers-$(uname -r)")
[ $WITH_GUI -eq 1 ] && pkgs+=(python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1)
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${pkgs[@]}" >/dev/null
echo "    ${pkgs[*]}"

# ---------------------------------------------------------------- 3. driver
NEED_REBOOT=0
if [ $WITH_DRIVER -eq 1 ]; then
  say "Building the fan driver (DKMS)"
  CUR_SERIES=$(uname -r | cut -d. -f1-2)       # e.g. 7.0
  WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
  mkdir -p "$WORK/src" "$WORK/p"
  # one patched source per kernel series: running + installed + EXTRA_SERIES
  SERIES_LIST=$( { uname -r; ls /lib/modules 2>/dev/null || true; printf '%s\n' "${EXTRA_SERIES[@]}"; } \
                 | cut -d. -f1-2 | grep -E '^[0-9]+\.[0-9]+$' | sort -uV )
  BUILT=()
  for s in $SERIES_LIST; do
    url="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/drivers/hwmon/dell-smm-hwmon.c?h=linux-${s}.y"
    if ! curl -sfL "$url" -o "$WORK/p/dell-smm-hwmon.c"; then
      [ "$s" = "$CUR_SERIES" ] && die "Could not download the driver source for kernel ${s}. Check the internet connection."
      warn "kernel $s: no source on kernel.org — skipped"; continue
    fi
    if grep -q '"Dell Pro Micro Plus QBM1250"' "$WORK/p/dell-smm-hwmon.c"; then
      echo "    kernel $s: already knows this model — no patch needed"; continue
    fi
    if ! patch -s -p3 -d "$WORK/p" < "$HERE/driver/dell-smm-hwmon-qbm1250.patch"; then
      [ "$s" = "$CUR_SERIES" ] && die "The patch does not apply to kernel ${s}. Please open an issue with your kernel version."
      warn "kernel $s: the patch does not apply — skipped"; continue
    fi
    cp "$WORK/p/dell-smm-hwmon.c" "$WORK/src/dell-smm-hwmon-$s.c"
    BUILT+=("$s")
    echo "    kernel $s: source linux-${s}.y (kernel.org) + patch"
  done

  # remove older and current versions of the package (upgrade / reinstall)
  for v in "${OLD_PKG_VERS[@]}" "$PKG_VER"; do
    if [ -n "$(dkms status -m "$PKG" -v "$v" 2>/dev/null || true)" ]; then
      dkms remove -m "$PKG" -v "$v" --all >/dev/null 2>&1 || true
    fi
    rm -rf "/usr/src/$PKG-$v"
  done
fi

if [ $WITH_DRIVER -eq 1 ] && [ ${#BUILT[@]} -eq 0 ]; then
  echo "    every kernel here already supports fan control on this model — using the kernel's own driver"
  modprobe -r dell_smm_hwmon 2>/dev/null || true
  modprobe dell_smm_hwmon 2>/dev/null || true
  WITH_DRIVER=0
fi

if [ $WITH_DRIVER -eq 1 ]; then
  # e.g. ^(6\.17|7\.0)\.  — dkms builds only for these series
  RE=$(IFS='|'; echo "${BUILT[*]}")
  RE="^(${RE//./'\.'})"'\.'
  conf=$(<"$HERE/driver/dkms.conf")
  printf '%s\n' "${conf//@SERIES_REGEX@/"$RE"}" > "$WORK/dkms.conf"
  echo 'obj-m := dell-smm-hwmon.o' > "$WORK/Makefile"
  install -d "/usr/src/$PKG-$PKG_VER/src"
  install -m 644 "$WORK/Makefile" "$WORK/dkms.conf" "/usr/src/$PKG-$PKG_VER/"
  install -m 755 "$HERE/driver/select-source.sh" "/usr/src/$PKG-$PKG_VER/"
  install -m 644 "$WORK"/src/*.c "/usr/src/$PKG-$PKG_VER/src/"
  echo "    kernel series: ${BUILT[*]}"

  # Secure Boot: modules must be signed with a key the firmware trusts (MOK).
  SB=0; [[ "$(mokutil --sb-state 2>/dev/null || true)" == *enabled* ]] && SB=1
  MOK_DER=/var/lib/shim-signed/mok/MOK.der
  if [ $SB -eq 1 ]; then
    echo "    Secure Boot is ON — the module will be signed with $MOK_DER"
    if [ ! -f "$MOK_DER" ]; then
      command -v update-secureboot-policy >/dev/null || die "No MOK key and no update-secureboot-policy. Create a MOK key first (see README)."
      update-secureboot-policy --new-key >/dev/null
    fi
  fi

  DEBIAN_FRONTEND=noninteractive dkms add -m "$PKG" -v "$PKG_VER" >/dev/null
  # build for every installed kernel of a prepared series that has its headers
  for k in $(ls /lib/modules); do
    ks=$(echo "$k" | cut -d. -f1-2)
    printf '%s\n' "${BUILT[@]}" | grep -qx "$ks" || continue
    if [ ! -e "/lib/modules/$k/build" ]; then
      warn "kernel $k: no headers — the driver is built when they are installed"; continue
    fi
    if DEBIAN_FRONTEND=noninteractive dkms install -m "$PKG" -v "$PKG_VER" -k "$k" --force >/dev/null 2>&1; then
      echo "    built for $k"
    elif [ "$k" = "$(uname -r)" ]; then
      die "Driver build failed: see /var/lib/dkms/$PKG/$PKG_VER/build/make.log"
    else
      warn "kernel $k: build failed (not the running kernel) — see /var/lib/dkms/$PKG/$PKG_VER/build/make.log"
    fi
  done
  dkms status -m "$PKG" -v "$PKG_VER" | sed 's/^/    /'

  # mokutil --test-key exits non-zero even when the key IS enrolled — judge by its message
  ENROLLED=0
  [[ "$(mokutil --test-key "$MOK_DER" 2>&1 || true)" == *"already enrolled"* ]] && ENROLLED=1
  if [ $SB -eq 1 ] && [ $ENROLLED -eq 0 ]; then
    say "Secure Boot: the signing key must be enrolled once"
    cat <<'EOF'
    You will now choose a ONE-TIME password (8+ characters, you need it only once).
    After that:
      1. Reboot.
      2. A blue "MOK management" screen appears (it waits 10 seconds — press a key).
      3. Enroll MOK -> Continue -> Yes -> type that password -> Reboot.
EOF
    mokutil --import "$MOK_DER" || die "mokutil --import failed"
    NEED_REBOOT=1
  else
    # swap the stock module for the patched one right now (service first: it hands the fan to BIOS)
    systemctl stop dellfan.service 2>/dev/null || true
    modprobe -r dell_smm_hwmon 2>/dev/null || true
    modprobe dell_smm_hwmon 2>/dev/null || warn "Could not load the new module now — it will load after a reboot."
  fi
fi

# ---------------------------------------------------------------- 4. service
say "Installing the service"
getent group dellfan >/dev/null || groupadd --system dellfan
install -d "$PREFIX" "$PREFIX/web" /etc/dellfan
install -m 755 "$HERE/service/dellfan.py" "$PREFIX/dellfan.py"
install -m 644 "$HERE/service/web/index.html" "$PREFIX/web/index.html"
install -m 644 "$HERE/service/dellfan.service" /etc/systemd/system/dellfan.service
if [ ! -f /etc/default/dellfan ]; then
  cat > /etc/default/dellfan <<'EOF'
# dellfan: starting values for the web page, used on the first run only.
# After that, change access and port in the app: Settings -> Web page.
DELLFAN_WEB=1
DELLFAN_HOST=127.0.0.1
DELLFAN_PORT=8095
EOF
fi
systemctl daemon-reload
systemctl enable dellfan.service >/dev/null 2>&1
if fan_hwmon >/dev/null && [ -e "$(fan_hwmon)/pwm1_enable" ]; then
  systemctl restart dellfan.service
  sleep 3
  systemctl is-active --quiet dellfan.service && echo "    dellfan is running" || warn "dellfan did not start: journalctl -u dellfan -b"
else
  [ $NEED_REBOOT -eq 1 ] || warn "Fan control is not available yet (pwm1_enable missing). It will start after a reboot."
fi

# ---------------------------------------------------------------- 5. desktop app
if [ $WITH_GUI -eq 1 ]; then
  say "Installing the desktop app"
  install -m 755 "$HERE/app/dellfan-gui.py" "$PREFIX/dellfan-gui.py"
  install -m 644 "$HERE/app/io.github.dellfan.DellFan.desktop" /usr/share/applications/
  install -m 644 "$HERE/app/io.github.dellfan.DellFan.svg" /usr/share/icons/hicolor/scalable/apps/
  gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
  if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    usermod -aG dellfan "$SUDO_USER"
    echo "    $SUDO_USER added to group 'dellfan' (takes effect after you log out and back in)"
  fi
fi

say "Done"
echo "    Web page:   http://127.0.0.1:8095 (in a browser on this computer)"
[ $WITH_GUI -eq 1 ] && echo "    App:        'Dell Pro Fan Control' in the applications menu"
echo "    Status:     systemctl status dellfan"
echo "    Remove:     sudo ./uninstall.sh"
[ $NEED_REBOOT -eq 1 ] && warn "Reboot now and enroll the key on the blue screen (see above)."
exit 0
