# 在 Hyper-V 上运行 DSM：VHDX 与 GPU-PV

当前脚本面向 Arc 4.2.11、SA6400 和第 2 代 Hyper-V 虚拟机，提供 DSM 7.2.1-69057 与 7.4.1-90080 两个构建选项。仓库提供补丁、工具和[技术教程](docs/guide.zh-CN.md)。Arc 镜像、DSM 安装包、编译后的内核模块和 NVIDIA 用户态文件由使用者在本机准备。

## 第一次准备启动盘

1. 按教程配置 Arc，选择 SA6400、目标 DSM 版本和 `legacy` 内核，完成一次构建。关闭测试虚拟机，准备一份独立的 Arc 启动 VHDX。若正在使用检查点，先导出或转换实际连接的启动盘，保留当前磁盘状态。
2. 按教程准备匹配 DSM 内核的 `hv_storvsc.ko`、`dxgkrnl.ko` 和修改后的 `disks.sh`。WSL2 中需要 Python 3.9 以上、`qemu-img`、`qemu-io`、`zstd`、`blkid` 和可用的 loop 挂载。
3. 在 WSL2 中，从仓库目录运行下列命令。替换示例路径，并保留足够空间存放启动盘副本和 ARC3 分区镜像。

```sh
sudo python3 -B tools/update-arc-vhdx.py \
  --dsm-version 7.2.1 \
  --source /mnt/d/dsm/arc-configured.vhdx \
  --output /mnt/d/dsm/arc-hyperv.vhdx \
  --work-directory /mnt/d/dsm/arc-build-01 \
  --storvsc /mnt/d/dsm/build/hv_storvsc.ko \
  --dxgkrnl /mnt/d/dsm/build/dxgkrnl.ko \
  --disks-script /mnt/d/dsm/build/disks.sh
```

使用 DSM 7.4.1 时，改用 `--dsm-version 7.4.1`，并提供为对应内核准备的模块和盘位脚本。工具会检查源盘上已构建的 DSM 版本。只需 VHDX 支持时可省略 `--dxgkrnl`。7.4.1 的数据盘识别曾在 Web Assistant 验证，GPU-PV 尚未在该版本验证。

将输出盘接到原有启动盘的 SCSI 位置，保留数据盘。首次开机会触发 Arc 重建，再加入内核模块和盘位脚本。后续 Arc 因 DSM 安装而重新生成 `initrd-dsm` 时，也会重新加入这些修改。升级 Arc 后如果替换了 `initrd-apex`，需要重新集成。构建脚本会保留输入 VHDX 和工作目录。

## 在 DSM 安装或更新 NVIDIA 用户态库

更新 Windows NVIDIA 驱动后，先确保 WSL2 中的 `/usr/lib/wsl/lib/nvidia-smi -L` 能正常运行。从具备 GPU 支持的 WSL2 发行版导出用户态文件：

```sh
python3 -B tools/export-nvidia-runtime.py /mnt/d/dsm/nvidia-runtime.tar
```

有多个完整驱动目录时，用 `--driver-directory` 指定实际使用的目录名。需要一起传送 CUDA 显存检查程序时，可先用 `cc -O2 -o cuda-memory-check tests/cuda-memory-check.c -ldl` 编译，再向导出命令传入 `--cuda-test ./cuda-memory-check`。

把生成的归档和 `scripts/install-nvidia-runtime.sh` 上传至 DSM。停止正在使用 GPU 的服务，以 root 安装，然后验证：

```sh
sh install-nvidia-runtime.sh --system-libs /path/to/nvidia-runtime.tar
/usr/lib/wsl/lib/nvidia-smi -L
# 如果导出时加入了 CUDA 显存检查程序，再运行这一条。
/usr/lib/wsl/lib/cuda-memory-check
```

`--system-libs` 在 `/usr/lib` 添加链接，保留库文件在 `/usr/lib/wsl`，让使用系统库搜索路径的程序无需设置 `LD_LIBRARY_PATH`。如果遇到现有的同名系统库，脚本会停止并保留原文件。只想在单个应用中使用 NVIDIA 库时，也可省略该选项，并为该应用指定 `LD_LIBRARY_PATH=/usr/lib/wsl/lib`。

脚本会保留原来的库目录，并打印备份路径。安装后重新启动 GPU 服务。更新 NVIDIA 用户态文件无需重建或更换 Arc 启动盘。此前打包过 NVIDIA 文件的测试镜像会在启动时复制旧版库，需先切换到按上节生成的新启动盘。要准备可分发的镜像，请使用未写入过 NVIDIA 文件的 Arc 源盘。
