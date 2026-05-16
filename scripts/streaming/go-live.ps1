# go-live.ps1 - validate prereqs then push the desktop to Twitch / YouTube / both.
# Usage:  .\go-live.ps1 twitch demo
#         .\go-live.ps1 multi  demo
# Keys are read from ~/.streaming/<platform>.key (gitignored).
param(
    [Parameter(Mandatory=$true)][ValidateSet('twitch','youtube','multi')]$Target,
    [string]$Scene = 'demo'
)
$ErrorActionPreference = 'Stop'
$keyDir = Join-Path $HOME '.streaming'

function Get-Key($name) {
    $p = Join-Path $keyDir "$name.key"
    if (-not (Test-Path $p) -or -not (Get-Content $p -Raw).Trim()) {
        throw "Missing/empty key: $p  (paste it from the platform dashboard first)"
    }
    return (Get-Content $p -Raw).Trim()
}

# --- prereqs ---
$nvenc = (& ffmpeg -hide_banner -encoders 2>&1 | Select-String 'h264_nvenc')
if (-not $nvenc) { throw 'h264_nvenc not available - aborting (do not software-encode while live)' }
Write-Host "[ok] NVENC h264_nvenc present"
$gpu = (& nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits) -as [int]
Write-Host "[info] GPU util ${gpu}% (evict Ollama/TRIBE if hot before a heavy stream)"

$vsrc = '-f gdigrab -framerate 60 -i desktop'
$asrc = '-f dshow -i audio="virtual-audio-capturer"'   # swap to your VoiceMeeter bus name
$venc = '-c:v h264_nvenc -preset p4 -profile:v high -rc cbr -g 120 -keyint_min 60 -pix_fmt yuv420p'
$aenc = '-c:a aac -b:a 160k -ar 48000'

switch ($Target) {
  'twitch' {
    $k = Get-Key twitch
    $cmd = "ffmpeg -y $vsrc $asrc $venc -b:v 6500k -maxrate 6500k -bufsize 13000k $aenc -f flv rtmp://live.twitch.tv/app/$k"
  }
  'youtube' {
    $k = Get-Key youtube
    $cmd = "ffmpeg -y $vsrc $asrc $venc -b:v 9000k -maxrate 9000k -bufsize 18000k $aenc -f flv rtmp://a.rtmp.youtube.com/live2/$k"
  }
  'multi' {
    $t = Get-Key twitch; $y = Get-Key youtube
    $tee = "[f=flv]rtmp://live.twitch.tv/app/$t|[f=flv]rtmp://a.rtmp.youtube.com/live2/$y"
    $cmd = "ffmpeg -y $vsrc $asrc $venc -b:v 6500k -maxrate 6500k -bufsize 13000k $aenc -f tee -map 0:v -map 1:a `"$tee`""
  }
}
Write-Host "[scene] $Scene  [target] $Target"
Write-Host "[run] $cmd"
Invoke-Expression $cmd
