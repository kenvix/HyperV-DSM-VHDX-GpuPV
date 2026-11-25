#!/bin/sh

if [ "$1" = patches ]; then
  LOG=/tmp/arc-gpu-pv.log
  {
    echo "GPU-PV startup"
    uname -r
    for name in pci-hyperv-intf pci-hyperv dxgkrnl; do
      sysname="$(printf '%s' "$name" | tr '-' '_')"
      if [ ! -d "/sys/module/$sysname" ]; then
        insmod "/usr/lib/modules/$name.ko"
        echo "insmod $name: $?"
      fi
    done
    for delay in 1 2 3 4; do
      [ -c /dev/dxg ] && break
      sleep 1
    done
    echo "GPU device:"
    ls -l /dev/dxg
    echo "VMBus devices:"
    for dev in /sys/bus/vmbus/devices/*; do
      [ -r "$dev/class_id" ] && printf '%s %s %s\n' "${dev##*/}" "$(cat "$dev/class_id")" "$(readlink "$dev/driver")"
    done
    echo "PCI devices:"
    lspci -nn
    echo "GPU kernel log:"
    dmesg | grep -Ei 'dxg|hyperv.pci|pci_hyperv|unknown symbol|vmbus' | tail -100
  } >"$LOG" 2>&1
fi
