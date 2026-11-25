# 在 Hyper-V 上安装 DSM 并使用 NVIDIA GPU

先让 DSM 安装器识别数据盘，再按需加入 GPU。本文使用 Arc 4.2.11、SA6400 和第 2 代 Hyper-V 虚拟机。DSM 7.2.1-69057 和 7.4.1-90080 都有 VHDX 识别记录。GPU-PV 和 CUDA 显存读写已在 7.2.1 的安装环境验证，7.4.1 的 GPU-PV 尚未验证。

如果只想安装 DSM，可从[发布页](https://github.com/kenvix/HyperV-DSM-VHDX-GpuPV/releases)下载预配置的引导 VHDX，然后按[仓库首页](../README.md#下载引导盘)操作。下面介绍自行构建的步骤。

## 1. 建立虚拟机并配置 Arc

1. 创建第 2 代虚拟机，关闭安全启动，连接可用的虚拟交换机。
2. 挂载 Arc 引导 VHDX，再挂载至少一块单独的数据 VHDX。记下引导盘的 SCSI 位置，之后只替换该位置的磁盘。
3. 启动 Arc，从控制台读取 IP 地址。在浏览器打开 `http://<虚拟机地址>:7080`，选择 SA6400、目标 DSM 版本和 `legacy` 内核，完成一次构建。`legacy` 是 Arc 的内核选项，虚拟机仍用 UEFI 启动。
4. 关闭虚拟机，准备一份独立的、已配置的启动 VHDX。如果连接的是检查点的 AVHDX，先导出或转换当前磁盘状态，再把独立 VHDX 交给工具。

[Arc 发布页](https://github.com/AuxXxilium/arc/releases/tag/4.2.11)

## 2. 准备存储补丁

构建工具需要两个文件，`hv_storvsc.ko` 和修改后的 `disks.sh`。将它们放在同一个目录，便于下一步传给工具。

从群晖的公开源码归档准备 SA6400 对应的 `epyc7002` 内核源码。此前测试使用了 7.4-90080 的 Linux 5.10.55 源码编译 StorVSC，并在 7.2.1 中验证过。模块版本标记可以用来排查明显不匹配，实际兼容性仍以客体启动结果为准。[群晖 GPL 源码归档](https://archive.synology.cn/download/ToolChain/Synology%20NAS%20GPL%20Source/7.4-90080)

在 WSL2 的 Linux 文件系统中准备工作目录。将源码解包到 `kernel/`，从 Arc 启动盘的 `ARC3` 分区取出 `initrd-dsm`。如果手边只有 VHDX，Arc 4.2.11 的这个分区布局可以用以下命令提取。请在新的工作目录执行：

```sh
qemu-img dd -f vhdx if=/path/to/arc-configured.vhdx of=arc3.img bs=1048576 skip=101 count=3700
debugfs -R 'dump /initrd-dsm initrd-dsm' arc3.img
mkdir initrd modules
cpio -id -D initrd -F initrd-dsm
patch -p1 -i /path/to/repo/patches/hyperv-storvsc-sa6400.patch
```

补丁同时修改 StorVSC 源码和 Arc 磁盘插件。然后用 `epyc7002` 配置编译模块：

```sh
cp kernel/synology/synoconfigs/epyc7002 kernel/.config
kernel/scripts/config --file kernel/.config -e HYPERVISOR_GUEST -e PARAVIRT \
  -m HYPERV -m HYPERV_STORAGE -e SYNO_PORT_MAPPING_V2 -d LOCALVERSION_AUTO
make -C kernel olddefconfig
make -C kernel -j"$(nproc)" LOCALVERSION=+ KBUILD_MODPOST_WARN=1 drivers/scsi/hv_storvsc.ko
cp kernel/drivers/scsi/hv_storvsc.ko initrd/usr/bin/disks.sh modules/
```

编译需要 GCC、Make、Flex、Bison 和内核构建依赖。如果新版 GCC 把旧版 `objtool` 的 `use-after-free` 警告视为错误，可为 Make 加上 `WERROR=0`、`HOSTCFLAGS=-Wno-error=use-after-free` 和 `EXTRA_WARNINGS=-Wno-error=use-after-free`。

为本项目的 7.4.1 引导盘编译时，需要在 `make olddefconfig` 之前启用 `CONFIG_UNWINDER_ORC`，并禁用 `CONFIG_UNWINDER_FRAME_POINTER`。该版本的 Arc `legacy` 内核使用 ORC。模块即使显示相同的 `5.10.55+` 版本标记，内部布局也必须和运行内核一致。

## 3. 按需准备 GPU-PV 模块

只需要 VHDX 时，跳到下一节。需要 GPU 时，先在一套已有 GPU 支持的 WSL2 发行版中运行 `/usr/lib/wsl/lib/nvidia-smi -L`，确认宿主机的 NVIDIA 环境可用。

以 Microsoft 的 `linux-msft-wsl-5.10.y` 分支为起点，将 `drivers/hv/dxgkrnl/` 和 `include/uapi/misc/d3dkmthk.h` 放到前一步内核源码树的对应位置。将 Microsoft 头文件中 `HV_GPUP_DXGK_GLOBAL_GUID` 和 `HV_GPUP_DXGK_VGPU_GUID` 的定义复制到 `dxgmodule.c` 的 `id_table` 前。这两个常量分别标识全局 GPU 通道和单块虚拟 GPU 的通道。[Microsoft WSL GPU 驱动源码](https://github.com/microsoft/WSL2-Linux-Kernel/tree/linux-msft-wsl-5.10.y/drivers/hv/dxgkrnl) · [Hyper-V 头文件](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-5.10.y/include/linux/hyperv.h)

```sh
kernel/scripts/config --file kernel/.config -e DMA_SHARED_BUFFER -e SYNC_FILE -m PCI_HYPERV
make -C kernel olddefconfig
make -C kernel -j"$(nproc)" M="$PWD/kernel/drivers/hv/dxgkrnl" \
  CONFIG_DXGKRNL=m LOCALVERSION=+ KBUILD_MODPOST_WARN=1 modules
cp kernel/drivers/hv/dxgkrnl/dxgkrnl.ko modules/
```

在管理员 PowerShell 中运行仓库脚本。若 Windows 只枚举到一块可分区的 NVIDIA 显卡，脚本会自动选用：

```powershell
.\scripts\Set-DsmGpuPartition.ps1 -VMName '<你的虚拟机名称>' -WhatIf
.\scripts\Set-DsmGpuPartition.ps1 -VMName '<你的虚拟机名称>'
```

如果有多块，先用 `-List` 查询，再用 `-InstancePath` 指定其中一块。虚拟机需要关机，首次添加适配器时不能有检查点。脚本默认配置 1 GiB 低位和 16 GiB 高位 MMIO，可用 `-LowMmioGiB` 和 `-HighMmioGiB` 调整。[GPU 分区适配器命令参考](https://learn.microsoft.com/en-us/powershell/module/hyper-v/add-vmgpupartitionadapter)

## 4. 生成启动盘并安装 DSM

从仓库目录运行下面的命令。替换源盘和模块目录。工具会识别 DSM 版本，并在源盘旁生成以 `-hyperv.vhdx` 结尾的新文件。

```sh
sudo python3 -B tools/update-arc-vhdx.py \
  --source /path/to/arc-configured.vhdx \
  --modules-directory /path/to/modules
```

如果需要 GPU，在命令末尾加 `--dxgkrnl /path/to/modules/dxgkrnl.ko`。运行结束后，将输出盘接到原引导盘的 SCSI 位置。启动虚拟机，在 DSM Web Assistant 中确认数据盘数量，再上传对应型号和构建号的 SA6400 完整安装包。安装重启后，确认可以进入 DSM，并在存储管理器中看到数据盘。

## 5. 在 DSM 安装或更新 NVIDIA 库

引导盘只负责让 DSM 创建 `/dev/dxg`。安装完成后，从宿主机 WSL2 导出用户态库，再在 DSM 安装。以后 Windows 更新 NVIDIA 驱动，重复这一步即可。完整命令见[仓库首页](../README.md#在-dsm-安装或更新-nvidia-用户态库)。

验证时先看 `/dev/dxg`，再运行 `/usr/lib/wsl/lib/nvidia-smi -L`。显卡能列出后，可用仓库的 `cuda-memory-check` 验证显存写入和读回。启用 `--system-libs` 后，使用系统库搜索路径的程序可以查找 `/usr/lib` 下的链接。如果系统已有同名库，安装脚本会停止并保留原文件。

## 为什么要修改盘位和 Arc 构建过程

VHDX 经由 Hyper-V 的虚拟 SCSI 控制器进入客体后，Linux 可以读取盘上的数据；DSM 还要把每块盘登记到 SA6400 的内部盘位。虚拟控制器在 VMBus 上，没有可供原有脚本匹配的 PCI 地址。补丁让 StorVSC 报告控制器标识和 LUN，再由 `disks.sh` 生成盘位。脚本用 `ARC3` 找到并排除启动盘，因为不同控制器上都可能有一块 LUN 0 的磁盘。[存储补丁](../patches/hyperv-storvsc-sa6400.patch)

安装 DSM 后，Arc 可能根据新写入的启动文件重新生成 `initrd-dsm`。直接修改生成后的文件会在这一步被覆盖。仓库的构建工具把补丁接入 `initrd-apex` 中的构建过程，Arc 每次为选定版本重建时都会加入这些文件。升级 Arc 若替换了 `initrd-apex`，需要对新版本重新集成。

GPU-PV 也分两部分。`dxgkrnl` 在 DSM 内核中，通过 VMBus 与 Windows 的 GPU 服务通信。NVIDIA 用户态库则通过 `/dev/dxg` 运行应用。两部分分开后，Arc 重建可以继续加载内核模块，NVIDIA 库仍可在已安装的 DSM 中单独更新。
