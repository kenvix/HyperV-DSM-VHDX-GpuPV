# 在 Hyper-V 上运行 DSM 7.2.1：从识别 VHDX 到共享 NVIDIA GPU

在 Hyper-V 中启动 Arc 后，我能打开 SA6400 的 DSM 安装页面，页面却提示没有硬盘。宿主机已经挂载两块 VHDX，Arc 自己也能看到它们。GPU 遇到了类似的问题。Hyper-V 可以分配 NVIDIA GPU 分区，DSM 里的程序还需要驱动和用户态库，才能使用这个分区。

这篇文章记录了两个问题的解决过程。实测环境为 Windows 11 企业版 Build 26200、RTX 4070 Laptop GPU、NVIDIA 驱动 621.35。虚拟机使用 Hyper-V 第 2 代，运行 Arc 4.2.11、SA6400 和 DSM 7.2.1-69057。最后，DSM 安装器识别了分接在两个控制器上的数据盘。另一台同版本虚拟机通过 GPU-PV 完成了 CUDA 显存写入和读回。

## 一、从两块 VHDX 开始

从 Arc 发布页下载 `arc-4.2.11.vhdx.zip`，解压得到引导 VHDX。另建一块空白的 64 GiB 动态 VHDX，用于安装 DSM。本文使用逻辑和物理扇区均为 512 字节的数据盘。 [Arc 4.2.11 发布页](https://github.com/AuxXxilium/arc/releases/tag/4.2.11)

| 项目 | 虚拟机配置 |
| --- | --- |
| 代数与固件 | 第 2 代，UEFI，关闭虚拟机的 Secure Boot |
| CPU 与内存 | 4 个虚拟处理器，8 GiB 静态内存 |
| 网络 | `Default Switch`，由 DHCP 分配地址 |
| SCSI `0:0` | Arc 引导 VHDX，设为第一启动项 |
| SCSI `0:1` | 空白数据 VHDX |
| 检查点 | 关闭自动检查点，便于后续添加 GPU 分区适配器 |

启动后，从虚拟机控制台读取地址，打开 `http://<虚拟机地址>:7080`。在 Arc 中选择 `SA6400`、DSM `7.2.1-69057` 和 `legacy` 内核，构建引导并启动。`legacy` 是 Arc 自定义内核的名称，虚拟机仍通过 UEFI 启动。Arc 的发布说明建议 Hyper-V 使用 VHDX 和第 2 代虚拟机。 [Arc 内核支持表](https://github.com/AuxXxilium/arc#supported) · [Arc 4.2.1 发布说明](https://xpenology.tech/release/view.php?f=4.2.1.txt)

接下来访问 `http://<虚拟机地址>:5000`。Arc 的自定义内核移除了帧缓冲控制台，切换到 DSM 后，Hyper-V 的连接窗口可能一直黑屏。网页能打开，就可以继续检查安装器状态。 [Arc 4.2.1 发布说明](https://xpenology.tech/release/view.php?f=4.2.1.txt)

## 二、磁盘已经挂载，为什么安装器还说没有硬盘

VHDX 是宿主机上的磁盘文件。Hyper-V 将它经由 VMBus 上的虚拟 SCSI 控制器交给客体，Linux 用 `hv_storvsc` 驱动访问它。DSM 安装器还要知道这块 Linux 块设备对应 SA6400 的哪个内部盘位，才会把它列为安装目标。

可以在宿主机上直接读取安装器的状态：

```powershell
$DsmAddress = '替换为虚拟机实际 IP'
$State = Invoke-RestMethod -Uri ('http://{0}:5000/webman/get_state.cgi' -f $DsmAddress)
if ($State -is [string]) {
    $State = $State | ConvertFrom-Json
}

[pscustomobject]@{
    Model   = $State.data.dsinfo.model
    Build   = $State.data.dsinfo.build_ver
    HasDisk = $State.data.has_disk
    Disks   = $State.data.dsinfo.disk_count
}
```

当时 Arc 配置环境能看到两块盘，DSM 安装器却返回 `has_disk=false`、`disk_count=0`。在同样的虚拟 SCSI 配置下，DSM 7.4.1 也出现了这个现象。Arc 的发布说明曾记录过一类相似的问题，修复位于 Redpill 模块的磁盘盘位识别部分。 [Arc 4.1.0 发布说明](https://xpenology.tech/release/view.php?f=4.1.0.txt)

这次要补的是两处相互依赖的逻辑。群晖内核通过 `syno_block_info` 报告盘位信息，其中包括控制器路径 `pciepath`、端口号 `ata_port_no` 和驱动类型 `driver`。标准 StorVSC 驱动不填写这些群晖字段。Arc 的 `disks.sh` 是 `arc-addons` 中的磁盘插件脚本，它会在缺少可用盘位信息时检查 PCI 控制器类别。Hyper-V 的虚拟 SCSI 控制器走 VMBus，在对应的设备路径上没有脚本所需的 PCI 地址。 [Arc 磁盘插件源码](https://github.com/AuxXxilium/arc-addons/blob/26.11.16/disks/all/usr/bin/disks.sh) · [群晖 GPL 内核源码归档](https://archive.synology.cn/download/ToolChain/Synology%20NAS%20GPL%20Source/7.4-90080)

## 三、让 StorVSC 和 DSM 盘位脚本使用相同的标识

[SA6400 Hyper-V 存储补丁](hyperv-storvsc-sa6400.patch) 修改了三个文件：

| 文件 | 修改 |
| --- | --- |
| `kernel/drivers/scsi/storvsc_drv.c` | 为 SCSI 主机模板注册 `syno_sdev_info_enum`，把 VMBus 控制器标识、SCSI channel 和 target 组成的路径，以及 LUN，写入 `syno_block_info`。 |
| `initrd/addons/addons.sh` | 在 Arc 的 `early` 阶段按需加载 `hv_vmbus`、`scsi_transport_fc` 和修改后的 `hv_storvsc`。 |
| `initrd/usr/bin/disks.sh` | 确认设备由 `hv_storvsc` 驱动后，使用驱动提供的路径和 LUN 生成内部盘位。 |

两个控制器可以各有一块 LUN 0 的磁盘，因此不能靠 LUN 判断哪块盘是引导盘。脚本通过 Arc 的 `ARC3` 分区找到它所属的磁盘，再把该磁盘排除。无法确认启动盘身份时，脚本跳过 VMBus 盘位登记，避免把引导盘交给 DSM 安装器。控制器标识、channel、target 和 LUN 共同区分其余磁盘。

### 编译存储模块

本次用群晖公开的 DSM 7.4-90080 `epyc7002` Linux 5.10.55 源码编译模块，再让 DSM 7.2.1 加载它。DSM 7.2.1 的 Arc 内核和 Redpill 模块继续沿用。存储模块的版本标记为 `5.10.55+ SMP mod_unload`。版本标记可以排查明显不匹配，是否能工作仍须用对应镜像启动验证。 [群晖 GPL 内核源码归档](https://archive.synology.cn/download/ToolChain/Synology%20NAS%20GPL%20Source/7.4-90080)

先关闭虚拟机，复制 Arc 引导 VHDX。在 Linux 中挂载副本的 `ARC3` 分区，取出 `/initrd-dsm`。initramfs 是内核在正式系统挂载前读取的临时文件系统。这版 Arc 把它保存为未压缩的 `newc` CPIO 存档。解包时使用 Linux 文件系统，以保留执行权限和符号链接。

以下命令从 Windows PowerShell 调用 WSL。先在 `$Work` 下准备 GPL 源码目录 `kernel/`、空目录 `initrd/`、取出的 `initrd-dsm`，以及文章所附的补丁。构建环境需要 GCC、Make、LZMA 解压工具、Flex、Bison 和 CPIO 等常用工具。

```powershell
$Distro = 'Ubuntu'
$Work = '/home/your-user/hyperv-dsm'
$Kernel = "$Work/kernel"

wsl.exe -d $Distro -u root --exec cpio -id -D "$Work/initrd" -F "$Work/initrd-dsm"
wsl.exe -d $Distro -u root --exec patch -d $Work -p1 -i "$Work/hyperv-storvsc-sa6400.patch"
wsl.exe -d $Distro --exec cp "$Kernel/synology/synoconfigs/epyc7002" "$Kernel/.config"
wsl.exe -d $Distro --exec "$Kernel/scripts/config" --file "$Kernel/.config" -e HYPERVISOR_GUEST -e PARAVIRT -m HYPERV -m HYPERV_STORAGE -e SYNO_PORT_MAPPING_V2 -d LOCALVERSION_AUTO
wsl.exe -d $Distro --exec make -C $Kernel olddefconfig
wsl.exe -d $Distro --exec make -C $Kernel -j8 'LOCALVERSION=+' 'KBUILD_MODPOST_WARN=1' drivers/scsi/hv_storvsc.ko
```

若新版 GCC 在编译旧版 `objtool` 时把 `use-after-free` 警告视为错误，可以为 Make 加上本次使用的三个参数 `WERROR=0`、`HOSTCFLAGS=-Wno-error=use-after-free` 和 `EXTRA_WARNINGS=-Wno-error=use-after-free`。独立编译模块可能缺少完整的内核符号表，因此还要在测试虚拟机中检查模块能否加载。

将生成的 `hv_storvsc.ko` 放到 `initrd/usr/lib/modules/hv_storvsc.ko`。用 CPIO 按 `newc` 格式重新打包，从 `initrd/` 开始收录全部内容，保留原有权限。下面的 PowerShell 示例把文件列表直接送给 WSL 中的 CPIO，避免在 Windows 和 Linux 之间传送二进制存档。

```powershell
wsl.exe -d $Distro -u root --exec install -m 0644 "$Kernel/drivers/scsi/hv_storvsc.ko" "$Work/initrd/usr/lib/modules/hv_storvsc.ko"
$Pack = @'
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
names = subprocess.check_output(["find", ".", "-print0"], cwd=root)
with Path(sys.argv[2]).open("xb") as archive:
    subprocess.run(
        ["cpio", "--null", "-o", "-H", "newc"],
        cwd=root,
        input=names,
        stdout=archive,
        check=True,
    )
'@
wsl.exe -d $Distro -u root --exec python3 -c $Pack "$Work/initrd" "$Work/initrd-dsm.new"
```

将新存档写回引导盘副本的 `ARC3` 分区，文件名为 `initrd-dsm`。写完后卸载分区，再将副本接回 Hyper-V。保留原始引导盘供恢复。以后从 Arc 界面重建引导时，需要重新应用对 initramfs 的修改。

先验证存储。我将一块 64 GiB 数据盘从 SCSI `0:1` 移到 `0:7`，另一块 24 GiB 数据盘接在 `1:0`。重启后，DSM 7.2.1 的安装器仍返回 `has_disk=true` 和 `disk_count=2`。如需安装，可以在 Web Assistant 手动上传 SA6400 的完整 `DSM_SA6400_69057.pat`。同一构建号的后续补丁在安装完成后通过 DSM 控制面板应用。 [SA6400 完整安装包归档](https://archive.synology.com/download/Os/DSM/7.2.1-69057-1-NanoPacked) · [群晖手动更新说明](https://kb.synology.com/en-us/DSM/tutorial/How_can_I_update_to_the_latest_DSM_version_from_an_older_version)

## 四、在 Hyper-V 中分配 NVIDIA GPU 分区

GPU-PV 是 GPU 半虚拟化。Hyper-V 把虚拟 GPU 交给客体，Windows 仍可以使用同一块物理显卡。两边同时运行任务时，会共享显存、计算和编码资源。本文所用的 Windows 客户端、笔记本 RTX 4070 和 DSM 组合不在 Microsoft 的 Windows Server GPU 分区支持清单内。 [Microsoft GPU 半虚拟化说明](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/gpu-paravirtualization) · [Windows Server GPU 分区支持清单](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/gpu-partitioning)

先确认 Windows 上的 `nvidia-smi` 能查询显卡，再在已有的 WSL2 发行版中运行 `wsl.exe -d Ubuntu --exec /usr/lib/wsl/lib/nvidia-smi -L`。后面会用到这套已经能工作的 NVIDIA 用户态文件。 [NVIDIA CUDA on WSL 指南](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)

在虚拟机关机、使用静态内存且没有检查点时，通过管理员 PowerShell 添加适配器。示例使用独立虚拟机 `DSM-GPU-PV-Test` 及修复存储后的引导盘副本。设备 ID 对应本次 RTX 4070 Laptop GPU。其他显卡先查看宿主机的枚举结果，再更换筛选值。

```powershell
$HostName = (Get-CimInstance Win32_ComputerSystem).Name
$Vm = Get-VM -ComputerName $HostName -Name 'DSM-GPU-PV-Test'
$Gpu = @(Get-VMHostPartitionableGpu -ComputerName $HostName |
    Where-Object Name -Match 'VEN_10DE&DEV_2820')

if ($Vm.State -ne 'Off' -or $Gpu.Count -ne 1) {
    throw '请确认虚拟机已关机，并且只匹配到一块目标显卡。'
}

Set-VM -VM $Vm -StaticMemory -GuestControlledCacheTypes $true
Set-VM -VM $Vm -LowMemoryMappedIoSpace 1GB -HighMemoryMappedIoSpace 16GB
Add-VMGpuPartitionAdapter -VM $Vm -InstancePath $Gpu[0].Name
Get-VMGpuPartitionAdapter -VM $Vm | Select-Object InstancePath, PartitionId
```

MMIO 为客体预留设备访问所需的地址空间。这里的 1 GiB 低位和 16 GiB 高位配置来自 Microsoft 示例。启动后再次查询适配器，可以确认 Hyper-V 是否已分配分区。还需要在客体内验证驱动和应用。 [Microsoft GPU 半虚拟化说明](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/gpu-paravirtualization) · [GPU 分区适配器命令参考](https://learn.microsoft.com/en-us/powershell/module/hyper-v/add-vmgpupartitionadapter)

## 五、为 DSM 编译 `dxgkrnl`

`dxgkrnl` 通过 VMBus 与 Windows 的 GPU 服务通信，并向 Linux 用户态公开 `/dev/dxg`。本次从 Microsoft 的 `linux-msft-wsl-5.10.y` 分支取得 `drivers/hv/dxgkrnl/` 和 `include/uapi/misc/d3dkmthk.h`，放入用于编译 StorVSC 的源码树中的对应目录。 [Microsoft WSL2 Linux 5.10 GPU 驱动源码](https://github.com/microsoft/WSL2-Linux-Kernel/tree/linux-msft-wsl-5.10.y/drivers/hv/dxgkrnl)

群晖源码的 `include/linux/hyperv.h` 缺少两个 GPU 通道定义。将 Microsoft 同分支头文件中 `HV_GPUP_DXGK_GLOBAL_GUID` 和 `HV_GPUP_DXGK_VGPU_GUID` 的原定义放到 `dxgmodule.c` 的 `id_table` 之前。前一个标识全局 GPU 通道，后一个标识每块虚拟 GPU 的通道。其余 VMBus 接口沿用 DSM 源码中的声明。 [Microsoft WSL2 的 Hyper-V 头文件](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-5.10.y/include/linux/hyperv.h)

模块还需要运行内核提供 DMA fence 和 `sync_file` 接口，用于通知 GPU 异步操作已经完成。本次 Arc 内核已具备这些接口，引导存档也已有 `pci-hyperv-intf.ko` 和 `pci-hyperv.ko`。构建目录需启用对应配置。 [WSL 内核发布说明](https://github.com/MicrosoftDocs/WSL/blob/main/WSL/kernel-release-notes.md)

```powershell
wsl.exe -d $Distro --exec "$Kernel/scripts/config" --file "$Kernel/.config" -e DMA_SHARED_BUFFER -e SYNC_FILE -m PCI_HYPERV
wsl.exe -d $Distro --exec make -C $Kernel olddefconfig
wsl.exe -d $Distro --exec make -C $Kernel -j8 "M=$Kernel/drivers/hv/dxgkrnl" 'CONFIG_DXGKRNL=m' 'LOCALVERSION=+' 'KBUILD_MODPOST_WARN=1' modules
```

把 `dxgkrnl.ko` 放进 `initrd/usr/lib/modules/`。在 `initrd/addons/addons.sh` 调用磁盘插件之后，增加一个由 `patches` 阶段执行的加载步骤。以下代码写入客体的启动脚本：

```sh
if [ "$1" = patches ]; then
  for module in pci-hyperv-intf pci-hyperv dxgkrnl; do
    sysname="$(printf '%s' "$module" | tr '-' '_')"
    if [ ! -d "/sys/module/$sysname" ]; then
      insmod "/usr/lib/modules/$module.ko"
    fi
  done
fi
```

启动后，到 `/sys/bus/vmbus/devices` 确认全局和虚拟 GPU 两种通道均已绑定 `dxgkrnl`，再检查 `/dev/dxg`。若设备没有出现，先看 `dmesg` 中的模块加载和 MMIO 分配，再回到宿主机核对分区适配器。

## 六、复制与宿主机驱动匹配的 NVIDIA 用户态文件

NVIDIA 用户态库通过 `libdxcore.so` 访问 `/dev/dxg`。已能使用 GPU 的 WSL2 里可以找到两组文件。放入 DSM initramfs 时，需保留它们的相对位置：

| 位置 | 本次使用的文件 |
| --- | --- |
| `/usr/lib/wsl/lib/` | `libdxcore.so`、`libd3d12.so`、`libd3d12core.so`、`libcuda.so.1`、`libnvidia-ml.so.1`、`nvidia-smi` |
| `/usr/lib/wsl/drivers/<本机驱动目录>/` | `libcuda.so.1.1`、`libnvidia-ml.so.1`、`libnvdxgdmal.so.1`、`libnvdiagclt.so.1`、`nvcubins.bin` |

驱动目录名随 Windows 驱动版本变化。可以在 WSL2 中用 `strace -f -e trace=openat` 观察 `nvidia-smi -L` 和 CUDA 程序实际打开的文件。复现时从自己的宿主机准备这些文件。 [NVIDIA CUDA on WSL 指南](https://docs.nvidia.com/cuda/wsl-user-guide/index.html) · [Linux GPU-PV 社区实现](https://github.com/Ripthulhu/easy-gpu-pv-linux)

我第一次在 DSM 中跑 `nvidia-smi -L` 时，已经能识别 RTX 4070，但 `cuInit(0)` 仍返回错误 `500`。同一个 CUDA 程序在 WSL2 中还会读取 `libnvdxgdmal.so.1`、`libnvdiagclt.so.1` 和 `nvcubins.bin`。将这三项加入对应驱动目录，再重新打包 initramfs 并启动，CUDA 初始化和显存操作均通过。显卡枚举和应用能否运行值得分别检查。

## 七、在同一次启动中验证磁盘和 GPU

用测试镜像启动后，在 DSM 安装环境给 `nvidia-smi` 设置 `LD_LIBRARY_PATH=/usr/lib/wsl/lib`。我把诊断命令放到启动脚本中，将结果写入安装环境的日志，便于从宿主机读取。GPU 枚举输出如下：

```text
GPU 0: NVIDIA GeForce RTX 4070 Laptop GPU
nvidia-smi status: 0
```

CUDA 部分使用一个通过 `dlopen` 加载 `libcuda.so.1` 的小程序。程序依次调用 `cuInit`、`cuDeviceGet`、`cuCtxCreate_v2`、`cuMemAlloc_v2`、`cuMemsetD32_v2` 和 `cuMemcpyDtoH_v2`，向显存写入一个固定值并读回，再释放显存和上下文。测试输出为：

```text
CUDA device 0 memory write/read: 0x047a1057
CUDA status: 0
```

同一次启动中，DSM 安装器仍识别一块数据盘，Windows 上的 `nvidia-smi` 也能查询同一块 RTX 4070。实测覆盖了安装环境中的 VHDX 识别、GPU-PV 设备枚举和 CUDA 显存读写。安装后的 DSM 应用兼容性、NVENC 编码，以及宿主机与客体并发使用 GPU 时的性能，仍需分别验证。

## 八、安装完成后，Arc 为什么又找不到数据盘

安装器能识别数据盘，只验证了当前启动镜像。安装 DSM 后，系统会把新的启动文件写入 Arc 的第二个分区。下一次开机时，Arc 4.2.11 检测到这些文件发生变化，就会调用 `livepatch` 重新生成 `initrd-dsm`。如果只修改生成后的文件，下一次构建就会覆盖这些修改。

这次重启后，`/sys/block` 仍能看到数据盘 `sata2`，`blkid` 也能读出它的 RAID 成员分区。但驱动报告的控制器路径变成了 `0000:00:00.0`，端口号也恢复为 `0`。新生成的 `disks.sh` 缺少 VMBus 盘位处理逻辑，DSM 因此没有登记数据盘，也未能组装系统卷。与此同时，GPU 启动脚本和 `/dev/dxg` 都消失了。两组修改原本都保存在同一份被覆盖的启动镜像里。

我把兼容文件保存在 Arc 的第三个分区，并在加载器自身的 `initrd-apex` 中修改 `ramdisk-patch.sh`。Arc 为 SA6400 构建 DSM 7.2.1-69057 时，会在重新打包前加入 StorVSC 模块、盘位脚本、GPU-PV 模块、启动脚本和用户态库。这样，同一加载器再次构建时就会把这些文件写入新的 `initrd-dsm`。如果升级 Arc 后 `initrd-apex` 被替换，需要重新集成。其他 DSM 构建号也需要分别验证。

我用安装后留下的启动文件触发了一次重建。串口显示 DSM 挂载了系统卷 `md0`，随后出现已安装系统的登录提示。安装后的网页管理端口当时尚未响应，因此存储管理界面和安装后的 GPU 功能仍待验证。

## 九、把 NVIDIA 用户态库移出引导盘

引导盘负责加载 StorVSC 和 `dxgkrnl`，并提供 DSM 所需的盘位信息。NVIDIA 用户态库由已安装的 DSM 从本地文件系统加载。将两部分分开后，首次构建 Arc 时不需要 NVIDIA 用户态文件。Windows 更新驱动后，只需从 WSL2 导出匹配的文件，在 DSM 内更新 `/usr/lib/wsl`，再验证 GPU 枚举和 CUDA。

新的构建脚本将兼容模块和盘位脚本写入独立的启动盘副本。库导出脚本则生成单独的归档，DSM 安装脚本会备份当前目录，再安装新文件。具体命令见[仓库首页](../README.md)。
