"""Export the current WSL NVIDIA userspace as a separate DSM archive."""

import argparse
from pathlib import Path
import tarfile

WSL = Path("/usr/lib/wsl")
LIBS = ("libcuda.so.1", "libnvidia-ml.so.1", "libdxcore.so",
        "libd3d12.so", "libd3d12core.so", "nvidia-smi")
DRIVER_FILES = ("libcuda.so.1.1", "libnvidia-ml.so.1", "libnvdxgdmal.so.1",
                "libnvdiagclt.so.1", "nvcubins.bin")


def select_driver(override):
    drivers = WSL / "drivers"
    if override:
        if Path(override).name != override or override in (".", ".."):
            raise ValueError("Specify the WSL driver directory's name")
        return drivers / override
    matches = sorted(p for p in drivers.iterdir() if p.is_dir()
                     and p.name.lower().startswith("nv")
                     and all((p / f).is_file() for f in DRIVER_FILES))
    if len(matches) != 1:
        names = ", ".join(p.name for p in matches)
        raise ValueError(f"Specify --driver-directory. Candidates: {names or '(none)'}")
    return matches[0]


def put_file(archive, source, name, mode=0o644):
    with source.open("rb") as stream:
        item = tarfile.TarInfo(name)
        item.size = source.stat().st_size
        item.mode = mode
        archive.addfile(item, stream)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--driver-directory")
    parser.add_argument("--cuda-test", type=Path)
    args = parser.parse_args()
    driver = select_driver(args.driver_directory)
    files = [(WSL / "lib" / n, f"lib/{n}", 0o755 if n == "nvidia-smi" else 0o644)
             for n in LIBS]
    files.extend((driver / n, f"drivers/{driver.name}/{n}", 0o644) for n in DRIVER_FILES)
    if args.cuda_test:
        files.append((args.cuda_test, "lib/cuda-memory-check", 0o755))
    for source, _, _ in files:
        if not source.is_file():
            parser.error(f"Missing file: {source}")
    with tarfile.open(args.output, "x") as archive:
        for source, name, mode in files:
            put_file(archive, source, name, mode)
    print(f"Exported {args.output} from {driver.name}")


if __name__ == "__main__":
    main()
