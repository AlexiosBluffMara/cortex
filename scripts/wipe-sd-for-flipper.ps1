# Run elevated. Wipes Disk 4 (the 64 GB SanDisk SD) and reformats as exFAT
# with label FLIPPER, mounted on F:. Output is logged so the parent shell can
# see what happened.

$logPath = "C:\Users\soumi\.cortex\logs\wipe-sd-for-flipper.log"
New-Item -ItemType Directory -Path (Split-Path $logPath) -Force | Out-Null
Start-Transcript -Path $logPath -Force | Out-Null

try {
    Write-Output "=== Disk 4 before ==="
    Get-Disk -Number 4 | Format-List Number,FriendlyName,PartitionStyle,Size
    Get-Partition -DiskNumber 4 | Format-Table

    # Use diskpart to fully clean + repartition + format as exFAT
    $script = @"
select disk 4
clean
convert mbr
create partition primary
format fs=exfat label=FLIPPER quick
assign letter=F
exit
"@
    $tmp = "$env:TEMP\diskpart-flipper.txt"
    Set-Content -Path $tmp -Value $script -Encoding ASCII

    Write-Output ""
    Write-Output "=== Running diskpart ==="
    $out = diskpart /s $tmp
    $out | ForEach-Object { Write-Output $_ }
    Remove-Item $tmp -ErrorAction SilentlyContinue

    Start-Sleep 3
    Write-Output ""
    Write-Output "=== Disk 4 after ==="
    Get-Disk -Number 4 | Format-List Number,FriendlyName,PartitionStyle,Size
    Get-Partition -DiskNumber 4 | Format-Table
    Get-Volume -DriveLetter F | Format-List DriveLetter,FileSystemLabel,FileSystem,Size,SizeRemaining
} finally {
    Stop-Transcript | Out-Null
}
