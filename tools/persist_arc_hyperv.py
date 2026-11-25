"""Install the Hyper-V payload into an offline Arc 4.2.11 ARC3 filesystem."""

import subprocess

DSM_BUILDS = {"7.2.1": "69057", "7.4.1": "90080"}


def archive_entries(data):
    offset = 0
    while True:
        start = offset
        header = data[offset:offset + 110]
        if header[:6] != b"070701":
            raise ValueError("Expected a newc archive")
        size = int(header[54:62], 16)
        name_size = int(header[94:102], 16)
        offset += 110
        name = data[offset:offset + name_size - 1].decode().removeprefix("./")
        offset = (offset + name_size + 3) & ~3
        contents = data[offset:offset + size]
        contents_offset = offset
        offset = (offset + size + 3) & ~3
        yield name, header, contents, start, contents_offset, offset
        if name == "TRAILER!!!":
            return


def verify_builder(partition):
    content = subprocess.check_output(["zstd", "-dc", partition / "initrd-apex"])
    if b"hyperv-payload.tar" not in content or b"hyperv-rebuild-once" not in content:
        raise ValueError("The Arc builder does not have the Hyper-V hooks")


def check_dsm_version(partition, version):
    expected = DSM_BUILDS[version].encode()
    entries = archive_entries((partition / "initrd-dsm").read_bytes())
    for name, _, contents, *_ in entries:
        if name == "etc/VERSION":
            fields = dict(line.split(b"=", 1) for line in contents.splitlines() if b"=" in line)
            actual = fields.get(b"buildnumber", b"").strip(b"\"'")
            if actual != expected:
                raise ValueError(
                    f"Selected DSM {version} expects build {expected.decode()}, "
                    f"but initrd-dsm reports {actual.decode(errors='replace') or '(missing)'}"
                )
            return
    raise ValueError("Cannot read the configured DSM version from initrd-dsm")


def install_builder(partition, version):
    apex = partition / "initrd-apex"
    if apex.with_suffix(".before-hyperv").exists():
        raise ValueError("Arc builder backup already exists")

    source = subprocess.check_output(["zstd", "-dc", apex])
    anchor = b"# Reassembly ramdisk\n"
    hook = br'''# Install the local Hyper-V compatibility payload before packing.
if [ "${MODEL}" = SA6400 ] && [ "${buildnumber}" = @DSM_BUILD@ ] &&
   [ -f "${PART3_PATH}/hyperv-payload.tar" ]; then
  if ! tar -xf "${PART3_PATH}/hyperv-payload.tar" -C "${RAMDISK_PATH}"; then
    echo "Could not install the Hyper-V ramdisk payload" >&2
    exit 1
  fi
  if ! grep -Fq 'Hyper-V storage modules' "${RAMDISK_PATH}/addons/addons.sh"; then
    grep -q '^/addons/disks\.sh ' "${RAMDISK_PATH}/addons/addons.sh" || exit 1
    sed -i '/^\/addons\/disks\.sh /i\if [ "${1}" = patches ]; then\n  for module in hv_vmbus scsi_transport_fc hv_storvsc; do\n    [ -d "/sys/module/${module}" ] || insmod "/usr/lib/modules/${module}.ko"\n  done\nfi' "${RAMDISK_PATH}/addons/addons.sh" || exit 1
    sed -i '/^\/addons\/disks\.sh /i\# Hyper-V storage modules' "${RAMDISK_PATH}/addons/addons.sh" || exit 1
    if [ -x "${RAMDISK_PATH}/addons/gpu-pv.sh" ]; then
      sed -i '/^\/addons\/disks\.sh /a\/addons/gpu-pv.sh "${1}"' "${RAMDISK_PATH}/addons/addons.sh" || exit 1
      grep -Fxq '/addons/gpu-pv.sh "${1}"' "${RAMDISK_PATH}/addons/addons.sh" || exit 1
    fi
  fi
fi

'''.replace(b"@DSM_BUILD@", DSM_BUILDS[version].encode())
    changes = []
    for name, header, contents, start, body, end in archive_entries(source):
        if name == "opt/arc/ramdisk-patch.sh":
            if contents.count(anchor) != 1:
                raise ValueError("Arc reassembly anchor is missing or ambiguous")
            revised = contents.replace(anchor, hook + anchor)
        elif name == "opt/arc/boot.sh":
            boot_anchor = b"# Check if DSM zImage/Ramdisk is changed,"
            if contents.count(boot_anchor) != 1:
                raise ValueError("Arc boot anchor is missing or ambiguous")
            revised = contents.replace(boot_anchor, b'''if [ -f "${PART3_PATH}/hyperv-rebuild-once" ]; then
  mv "${PART3_PATH}/hyperv-rebuild-once" "${PART3_PATH}/hyperv-rebuild-requested" || exit 1
  livepatch || exit 1
fi

''' + boot_anchor)
        else:
            continue
        subprocess.run(["/bin/bash", "-n"], input=revised, check=True)
        record = (header[:54] + f"{len(revised):08x}".encode()
                  + source[start + 62:body] + revised + bytes(-len(revised) % 4))
        changes.append((start, end, record))
    if len(changes) != 2:
        raise ValueError("Arc build and boot scripts are required")
    result = source
    for start, end, record in reversed(changes):
        result = result[:start] + record + result[end:]

    staged = apex.with_suffix(".hyperv-new")
    with staged.open("xb") as output:
        subprocess.run(["zstd", "-q", "-c"], input=result, stdout=output, check=True)
    apex.rename(apex.with_suffix(".before-hyperv"))
    staged.rename(apex)
