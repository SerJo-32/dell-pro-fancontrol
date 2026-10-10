#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# dellfan uninstaller. The fan goes back to automatic BIOS control.
#
#   sudo ./uninstall.sh            remove everything, keep your settings in /etc/dellfan
#   sudo ./uninstall.sh --purge    also remove settings and the 'dellfan' group
#
# The Secure Boot key (MOK) is left alone — other DKMS modules may use it.
set -euo pipefail

PKG=dell-smm-hwmon-qbm1250
PKG_VER=1.0
PREFIX=/opt/dellfan
PURGE=0
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;;
    -h|--help) sed -n '4,9p' "$0"; exit 0 ;;
    *) echo "Unknown option: $a" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo ./uninstall.sh" >&2; exit 1; }

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

say "Stopping the service (fan -> BIOS)"
systemctl disable --now dellfan.service 2>/dev/null || true
rm -f /etc/systemd/system/dellfan.service
systemctl daemon-reload

say "Removing files"
rm -rf "$PREFIX"
rm -f /usr/share/applications/io.github.dellfan.DellFan.desktop \
      /usr/share/icons/hicolor/scalable/apps/io.github.dellfan.DellFan.svg
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true

if command -v dkms >/dev/null && dkms status -m "$PKG" -v "$PKG_VER" 2>/dev/null | grep -q .; then
  say "Removing the patched driver"
  dkms remove -m "$PKG" -v "$PKG_VER" --all >/dev/null
  rm -rf "/usr/src/$PKG-$PKG_VER"
  # back to the kernel's own module (no manual fan control)
  modprobe -r dell_smm_hwmon 2>/dev/null || true
  modprobe dell_smm_hwmon 2>/dev/null || true
fi

if [ $PURGE -eq 1 ]; then
  say "Removing settings"
  rm -rf /etc/dellfan /etc/default/dellfan
  getent group dellfan >/dev/null && groupdel dellfan
else
  echo "    settings kept: /etc/dellfan, /etc/default/dellfan (use --purge to remove)"
fi

say "Done — the fan is under BIOS control"
