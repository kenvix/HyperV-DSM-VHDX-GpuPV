#!/bin/sh
set -eu

if [ "$#" -ne 1 ] || [ "$(id -u)" -ne 0 ]; then
  echo "Usage (as root): sh install-nvidia-runtime.sh /path/to/runtime.tar" >&2
  exit 1
fi

archive=$1
target=/usr/lib/wsl
staging=$(mktemp -d /usr/lib/.wsl-runtime.XXXXXXXX)
incoming="$staging/incoming"
backup="$staging/previous"
umask 022
mkdir "$incoming"
tar -tf "$archive" >"$staging/files"
tar -tvf "$archive" >"$staging/types"
awk '
  !/^(lib\/[A-Za-z0-9._-]+|drivers\/[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+)$/ ||
  /(^|\/)\.{1,2}(\/|$)/ || seen[$0]++ { exit 1 }
  END { if (NR == 0) exit 1 }
' "$staging/files"
awk 'substr($0, 1, 1) != "-" { exit 1 }' "$staging/types"
tar -xf "$archive" -C "$incoming"
for file in nvidia-smi libcuda.so.1 libnvidia-ml.so.1 libdxcore.so; do
  test -s "$incoming/lib/$file"
done
set -- "$incoming"/drivers/*
test "$#" -eq 1
test -d "$1"
for file in libcuda.so.1.1 libnvidia-ml.so.1 libnvdxgdmal.so.1 libnvdiagclt.so.1 nvcubins.bin; do
  test -s "$1/$file"
done
chmod 755 "$incoming"
chmod 755 "$incoming/lib/nvidia-smi"
if [ -e "$target" ] || [ -L "$target" ]; then
  mv "$target" "$backup"
fi
if ! mv "$incoming" "$target"; then
  if [ -e "$backup" ] || [ -L "$backup" ]; then mv "$backup" "$target"; fi
  exit 1
fi
printf 'Installed %s. Previous files, if any: %s\n' "$target" "$backup"
