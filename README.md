# 在 Hyper-V 上运行 DSM：VHDX 与 GPU-PV

本项目在第 2 代 Hyper-V 虚拟机中用 Arc 4.2.11 引导 SA6400，支持 DSM 7.2.1-69057 和 7.4.1-90080。你可以下载预配置的引导盘，也可以用仓库的补丁自行构建。

## 下载引导盘

从 [GitHub Releases](https://github.com/kenvix/HyperV-DSM-VHDX-GpuPV/releases) 下载对应版本的 VHDX。

| DSM 版本 | 引导盘功能 |
| --- | --- |
| 7.2.1-69057 | Hyper-V 数据盘识别，包含 GPU-PV 内核模块 |
| 7.4.1-90080 | Hyper-V 数据盘识别 |

1. 创建第 2 代虚拟机，关闭安全启动，并连接虚拟交换机。
2. 将下载的 VHDX 接到 SCSI 控制器并设为第一启动项。再添加一块独立的数据 VHDX。
3. 启动虚拟机，在 DSM Web Assistant 确认可用数据盘，上传对应版本的 SA6400 完整 `.pat` 安装包。

发布的两份引导盘已分别启动到 Web Assistant，并识别一块独立数据盘。安装过程需要另备 DSM 完整安装包。使用 GPU-PV 时，还需要在 Hyper-V 分配 NVIDIA GPU 分区，并在 DSM 内安装与宿主机驱动配套的用户态库。

需要自己编译模块时，请看[安装教程](docs/guide.zh-CN.md)。只想更新 GPU 库时，可直接跳到最后一节。

## 生成启动盘

准备已配置的 Arc VHDX，以及存放 `hv_storvsc.ko` 和修改版 `disks.sh` 的目录。在 WSL2 中需要 Python 3.9 以上、`qemu-img`、`qemu-io`、`zstd`、`blkid` 和可用的 loop 挂载。在仓库目录执行：

```sh
sudo python3 -B tools/update-arc-vhdx.py \
  --source /path/to/arc-configured.vhdx \
  --modules-directory /path/to/modules
```

脚本会识别源盘上的 DSM 版本，在同一目录生成 `arc-configured-hyperv.vhdx` 和工作目录。需要 GPU 时，再加 `--dxgkrnl /path/to/modules/dxgkrnl.ko`。如需核对预期版本，可以指定 `--dsm-version`。

关闭虚拟机，把输出盘接到原引导盘的 SCSI 位置。首次开机会触发 Arc 重建。数据盘和原引导盘可保留。升级 Arc 如果替换了 `initrd-apex`，需重新集成补丁。

## 分配 NVIDIA GPU

在管理员 PowerShell 中运行。先预览，再执行分配：

```powershell
.\scripts\Set-DsmGpuPartition.ps1 -VMName '<你的虚拟机名称>' -WhatIf
.\scripts\Set-DsmGpuPartition.ps1 -VMName '<你的虚拟机名称>'
```

只有一块可分区的 NVIDIA GPU 时，脚本会自动选择。有多块时，先用 `-List` 查询，再用 `-InstancePath` 指定。7.2.1 的安装环境已验证 GPU 枚举和 CUDA 显存读写。7.4.1 已验证数据盘识别，GPU-PV 尚未验证。

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
