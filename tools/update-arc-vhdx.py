"""Create an offline Arc VHDX copy with Hyper-V kernel modules and no NVIDIA libraries."""

import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile

from persist_arc_hyperv import DSM_BUILDS, check_dsm_version, install_builder, verify_builder

REPO = Path(__file__).resolve().parents[1]


def put_file(archive, source, name, mode=0o644):
    with source.open("rb") as stream:
        item = tarfile.TarInfo(name)
        item.size = source.stat().st_size
        item.mode = mode
        archive.addfile(item, stream)


def build_payload(partition, args):
    existing = partition / "hyperv-payload.tar"
    staged = partition / "hyperv-payload.next.tar"
    if existing.exists():
        raise ValueError("Use an Arc source disk without a previously installed payload")
    with tarfile.open(staged, "x") as result:
        put_file(result, args.storvsc, "usr/lib/modules/hv_storvsc.ko")
        put_file(result, args.disks_script, "usr/bin/disks.sh", 0o755)
        if args.dxgkrnl:
            put_file(result, args.dxgkrnl, "usr/lib/modules/dxgkrnl.ko")
            put_file(result, REPO / "scripts/gpu-pv.sh", "addons/gpu-pv.sh", 0o755)
    staged.replace(existing)
    (partition / "hyperv-rebuild-once").touch(exist_ok=True)


def run(*argv, **kwargs):
    return subprocess.run(argv, check=True, **kwargs)


def partition_geometry(disk, workspace):
    sector = workspace / "mbr.bin"
    run("qemu-img", "dd", "-f", "vhdx", f"if={disk}", f"of={sector}", "bs=512", "count=1")
    mbr = sector.read_bytes()
    if len(mbr) != 512 or mbr[-2:] != b"\x55\xaa":
        raise ValueError("Expected an Arc MBR image")
    start, size = struct.unpack_from("<II", mbr, 446 + 2 * 16 + 8)
    if (start, size) != (206848, 7370752):
        raise ValueError("This Arc 4.2.11 layout has an unexpected ARC3 extent")
    return start * 512, size * 512


def write_partition(disk, image, offset, workspace):
    chunk = workspace / "write-buffer.bin"
    with image.open("rb") as source:
        while data := source.read(64 * 1024 * 1024):
            chunk.write_bytes(data)
            run("qemu-io", "-f", "vhdx", "-c",
                f"write -q -s {chunk.name} {offset} {len(data)}", str(disk), cwd=workspace)
            offset += len(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--work-directory", required=True, type=Path)
    parser.add_argument("--dsm-version", choices=DSM_BUILDS, default="7.2.1")
    parser.add_argument("--dxgkrnl", type=Path, help="Optional GPU-PV kernel module")
    for flag in ("storvsc", "disks-script"):
        parser.add_argument("--" + flag, required=True, type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as root in the WSL distribution to mount ARC3")
    args.source = args.source.resolve(strict=True)
    args.output = args.output.resolve()
    workspace = args.work_directory.resolve()
    if args.output.exists() or args.output.suffix.lower() != ".vhdx":
        parser.error("--output must be a new .vhdx file")
    if args.source.suffix.lower() != ".vhdx":
        parser.error("--source must be a standalone, offline .vhdx")
    workspace.mkdir()
    offset, size = partition_geometry(args.source, workspace)
    block = 1024 * 1024
    image = workspace / "arc3.img"
    run("qemu-img", "dd", "-f", "vhdx", f"if={args.source}", f"of={image}",
        f"bs={block}", f"skip={offset // block}", f"count={(offset + size) // block}")
    label = subprocess.check_output(["blkid", "-p", "-s", "LABEL", "-o", "value", image], text=True).strip()
    if image.stat().st_size != size or label != "ARC3":
        raise ValueError("Extracted partition size or label does not match ARC3")
    partition = workspace / "mounted"
    partition.mkdir()
    run("mount", "-o", "loop", image, partition)
    try:
        check_dsm_version(partition, args.dsm_version)
        install_builder(partition, args.dsm_version)
        verify_builder(partition)
        build_payload(partition, args)
    finally:
        run("umount", partition)
    with args.source.open("rb") as source, args.output.open("xb") as output:
        shutil.copyfileobj(source, output, length=16 * block)
    write_partition(args.output, image, offset, workspace)
    print(f"Created {args.output}")


if __name__ == "__main__":
    main()
