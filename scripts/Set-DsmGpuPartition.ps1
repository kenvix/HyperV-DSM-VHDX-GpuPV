#Requires -Modules Hyper-V
[CmdletBinding(SupportsShouldProcess, DefaultParameterSetName = 'Configure')]
param(
    [Parameter(Mandatory, ParameterSetName = 'List')]
    [switch]$List,

    [Parameter(Mandatory, ParameterSetName = 'Configure')]
    [string]$VMName,

    [Parameter(ParameterSetName = 'Configure')]
    [string]$InstancePath,

    [Parameter(ParameterSetName = 'Configure')]
    [ValidateRange(1, 64)]
    [int]$LowMmioGiB = 1,

    [Parameter(ParameterSetName = 'Configure')]
    [ValidateRange(1, 256)]
    [int]$HighMmioGiB = 16
)

$ErrorActionPreference = 'Stop'
$localComputer = [Environment]::MachineName
$candidates = @(Get-VMHostPartitionableGpu -ComputerName $localComputer |
    Where-Object Name -Match 'VEN_10DE(?:&|#)')
if ($List) {
    $candidates | Select-Object @{Name='InstancePath'; Expression={$_.Name}}
    return
}

if ($InstancePath) {
    $candidates = @($candidates | Where-Object Name -EQ $InstancePath)
}
if ($candidates.Count -ne 1) {
    throw 'Select one NVIDIA device. Run this script with -List and pass its exact InstancePath.'
}
$chosen = $candidates[0].Name
$vm = Get-VM -ComputerName $localComputer -Name $VMName
if ($vm.State -ne 'Off' -or $vm.Generation -ne 2) {
    throw 'The target must be a powered-off Generation 2 virtual machine.'
}
$existing = @(Get-VMGpuPartitionAdapter -VM $vm)
if ($existing.Count -gt 0) {
    if ($existing.Count -eq 1 -and $existing[0].InstancePath -eq $chosen) {
        $existing[0]
        return
    }
    throw 'The VM already has a different GPU partition adapter.'
}
if (@(Get-VMSnapshot -VMName $vm.Name -ComputerName $localComputer).Count -gt 0) {
    throw 'Use a VM without checkpoints when adding a GPU partition adapter.'
}

if ($PSCmdlet.ShouldProcess($vm.Name, "Assign NVIDIA GPU partition and configure MMIO ($chosen)")) {
    $settings = @{
        VM = $vm
        StaticMemory = $true
        GuestControlledCacheTypes = $true
        LowMemoryMappedIoSpace = $LowMmioGiB * 1GB
        HighMemoryMappedIoSpace = $HighMmioGiB * 1GB
    }
    Set-VM @settings
    Add-VMGpuPartitionAdapter -VM $vm -InstancePath $chosen
    Get-VMGpuPartitionAdapter -VM $vm
}
