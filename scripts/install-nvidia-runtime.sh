#!/bin/sh
set -eu

system_libs=false
if [ "${1:-}" = "--system-libs" ]; then
  system_libs=true
  shift
fi
if [ "$#" -ne 1 ]; then
  echo "Usage (as root): sh install-nvidia-runtime.sh [--system-libs] /path/to/runtime.tar" >&2
  exit 1
fi
if command -v id >/dev/null 2>&1; then
  user_id=$(id -u 2>/dev/null || printf '%s' unknown)
  if [ "$user_id" != 0 ]; then
    echo "Usage (as root): sh install-nvidia-runtime.sh [--system-libs] /path/to/runtime.tar" >&2
    exit 1
  fi
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
for file in nvidia-smi libcuda.so.1 libnvidia-ml.so.1 libdxcore.so libd3d12.so libd3d12core.so; do
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
system_library_links() {
  printf '%s\n' \
    'libcuda.so:libcuda.so.1' 'libcuda.so.1:libcuda.so.1' \
    'libnvidia-ml.so:libnvidia-ml.so.1' 'libnvidia-ml.so.1:libnvidia-ml.so.1' \
    'libdxcore.so:libdxcore.so' 'libd3d12.so:libd3d12.so' \
    'libd3d12core.so:libd3d12core.so'
}
if "$system_libs"; then
  system_library_links | while IFS=: read -r name library; do
    link="/usr/lib/$name"
    destination="$target/lib/$library"
    if [ -e "$link" ] || [ -L "$link" ]; then
      if [ "$(readlink "$link" 2>/dev/null || true)" != "$destination" ]; then
        printf 'Existing system library conflicts with %s\n' "$link" >&2
        exit 1
      fi
    fi
  done
fi
if [ -e "$target" ] || [ -L "$target" ]; then
  mv "$target" "$backup"
fi
if ! mv "$incoming" "$target"; then
  if [ -e "$backup" ] || [ -L "$backup" ]; then mv "$backup" "$target"; fi
  exit 1
fi
if "$system_libs"; then
  system_library_links | while IFS=: read -r name library; do
    link="/usr/lib/$name"
    [ -L "$link" ] || ln -s "$target/lib/$library" "$link"
  done
  if command -v ldconfig >/dev/null 2>&1; then
    ldconfig || echo 'Could not refresh the system library cache' >&2
  fi
fi
printf 'Installed %s. Previous files, if any: %s\n' "$target" "$backup"
