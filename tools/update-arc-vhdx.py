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


def build_payload(partition, args, version):
    existing = partition / f"hyperv-payload-{DSM_BUILDS[version]}.tar"
    staged = existing.with_suffix(".next.tar")
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
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--dsm-version", choices=DSM_BUILDS, help="Check the detected version")
    parser.add_argument("--modules-directory", type=Path, help="Directory containing hv_storvsc.ko and disks.sh")
    parser.add_argument("--dxgkrnl", type=Path, help="Optional GPU-PV kernel module")
    for flag in ("storvsc", "disks-script"):
        parser.add_argument("--" + flag, type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as root in the WSL distribution to mount ARC3")
    args.source = args.source.resolve(strict=True)
    args.output = (args.output or args.source.with_name(f"{args.source.stem}-hyperv.vhdx")).resolve()
    workspace = (args.work_directory or args.output.with_suffix(".work")).resolve()
    if args.modules_directory:
        args.storvsc = args.storvsc or args.modules_directory / "hv_storvsc.ko"
        args.disks_script = args.disks_script or args.modules_directory / "disks.sh"
    for name, path in (("StorVSC module", args.storvsc), ("disk script", args.disks_script),
                       ("GPU-PV module", args.dxgkrnl)):
        if name == "GPU-PV module" and path is None:
            continue
        if path is None or not path.is_file():
            parser.error(f"{name} is missing. Provide --modules-directory or explicit file paths.")
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
        version = check_dsm_version(partition, args.dsm_version)
        print(f"Detected DSM {version} ({DSM_BUILDS[version]})")
        install_builder(partition, version)
        verify_builder(partition)
        build_payload(partition, args, version)
    finally:
        run("umount", partition)
    with args.source.open("rb") as source, args.output.open("xb") as output:
        shutil.copyfileobj(source, output, length=16 * block)
    write_partition(args.output, image, offset, workspace)
    print(f"Created {args.output}")


if __name__ == "__main__":
    main()
