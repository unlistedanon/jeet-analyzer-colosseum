param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Narrate", "Encode")]
    [string] $Action,
    [Parameter(Mandatory = $true)]
    [string] $ConfigPath,
    [string] $OutputDirectory,
    [string] $FramesDirectory,
    [string] $AudioPath,
    [string] $VideoPath
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$buildRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot "build"))

function Assert-BuildPath([string] $PathValue) {
    $resolved = [IO.Path]::GetFullPath($PathValue)
    if (-not $resolved.StartsWith($buildRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Generated media must remain under the repository build directory."
    }
    return $resolved
}

$configuration = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$captions = @($configuration.scenes | ForEach-Object { $_.captions })

if ($Action -eq "Narrate") {
    Add-Type -AssemblyName System.Speech
    $destination = Assert-BuildPath $OutputDirectory
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    $synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $voices = @($synthesizer.GetInstalledVoices() | Where-Object { $_.Enabled })
    $voice = $voices | Where-Object { $_.VoiceInfo.Name -eq "Microsoft David Desktop" } | Select-Object -First 1
    if (-not $voice) { $voice = $voices | Where-Object { $_.VoiceInfo.Culture.Name -eq "en-US" } | Select-Object -First 1 }
    if (-not $voice) { throw "No enabled local English System.Speech voice is available." }
    $synthesizer.SelectVoice($voice.VoiceInfo.Name)
    $synthesizer.Rate = [int]$configuration.speech_rate
    try {
        for ($index = 0; $index -lt $captions.Count; $index++) {
            $path = Join-Path $destination ("segment-{0:D3}.wav" -f ($index + 1))
            $synthesizer.SetOutputToWaveFile($path)
            $synthesizer.Speak(([string]$captions[$index].text).Replace("`n", " "))
            $synthesizer.SetOutputToNull()
        }
    } finally {
        $synthesizer.Dispose()
    }
    Write-Output ("VOICE=" + $voice.VoiceInfo.Name)
    Write-Output ("SEGMENTS=" + $captions.Count)
    exit 0
}

Add-Type -AssemblyName System.Runtime.WindowsRuntime

function Await-Operation($Operation, [Type] $ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq "AsTask" -and $_.IsGenericMethod -and
            $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1"
        } | Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}

function Await-ProgressOperation($Operation, [Type] $ResultType, [Type] $ProgressType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq "AsTask" -and $_.IsGenericMethod -and
            $_.GetGenericArguments().Count -eq 2 -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperationWithProgress``2"
        } | Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType, $ProgressType).Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}

$frames = Get-ChildItem -LiteralPath (Assert-BuildPath $FramesDirectory) -Filter "frame-*.png" -File | Sort-Object Name
if ($frames.Count -ne $captions.Count) {
    throw "Expected $($captions.Count) captured frames, found $($frames.Count)."
}
$audio = Assert-BuildPath $AudioPath
$video = Assert-BuildPath $VideoPath
New-Item -ItemType Directory -Path (Split-Path -Parent $video) -Force | Out-Null

$storageFileType = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$storageFolderType = [Windows.Storage.StorageFolder, Windows.Storage, ContentType = WindowsRuntime]
$mediaClipType = [Windows.Media.Editing.MediaClip, Windows.Media.Editing, ContentType = WindowsRuntime]
$audioTrackType = [Windows.Media.Editing.BackgroundAudioTrack, Windows.Media.Editing, ContentType = WindowsRuntime]
$compositionType = [Windows.Media.Editing.MediaComposition, Windows.Media.Editing, ContentType = WindowsRuntime]
$profileType = [Windows.Media.MediaProperties.MediaEncodingProfile, Windows.Media.MediaProperties, ContentType = WindowsRuntime]
$qualityType = [Windows.Media.MediaProperties.VideoEncodingQuality, Windows.Media.MediaProperties, ContentType = WindowsRuntime]
$collisionType = [Windows.Storage.CreationCollisionOption, Windows.Storage, ContentType = WindowsRuntime]
$trimmingType = [Windows.Media.Editing.MediaTrimmingPreference, Windows.Media.Editing, ContentType = WindowsRuntime]
$failureType = [Windows.Media.Transcoding.TranscodeFailureReason, Windows.Media.Transcoding, ContentType = WindowsRuntime]
$videoPropertiesType = [Windows.Storage.FileProperties.VideoProperties, Windows.Storage, ContentType = WindowsRuntime]
$clipCollectionType = [System.Collections.Generic.ICollection``1].MakeGenericType($mediaClipType)
$audioCollectionType = [System.Collections.Generic.ICollection``1].MakeGenericType($audioTrackType)

$composition = New-Object $compositionType.FullName
for ($index = 0; $index -lt $frames.Count; $index++) {
    $image = Await-Operation ($storageFileType::GetFileFromPathAsync($frames[$index].FullName)) $storageFileType
    $clip = Await-Operation ($mediaClipType::CreateFromImageFileAsync($image, [TimeSpan]::FromSeconds([double]$captions[$index].duration_seconds))) $mediaClipType
    $null = $clipCollectionType.GetMethod("Add").Invoke($composition.Clips, @($clip))
}
$audioFile = Await-Operation ($storageFileType::GetFileFromPathAsync($audio)) $storageFileType
$audioTrack = Await-Operation ($audioTrackType::CreateFromFileAsync($audioFile)) $audioTrackType
$null = $audioCollectionType.GetMethod("Add").Invoke($composition.BackgroundAudioTracks, @($audioTrack))
$outputFolder = Await-Operation ($storageFolderType::GetFolderFromPathAsync((Split-Path -Parent $video))) $storageFolderType
$output = Await-Operation ($outputFolder.CreateFileAsync((Split-Path -Leaf $video), $collisionType::ReplaceExisting)) $storageFileType
$profile = $profileType::CreateMp4($qualityType::HD1080p)
$profile.Video.Bitrate = 5000000
$profile.Audio.Bitrate = 128000
$operation = $composition.RenderToFileAsync($output, $trimmingType::Precise, $profile)
$failure = Await-ProgressOperation $operation $failureType ([double])
if ([string]$failure -ne "None") { throw "Windows MediaComposition failed: $failure" }
$properties = Await-Operation ($output.Properties.GetVideoPropertiesAsync()) $videoPropertiesType
Write-Output ("TRANSCODE_RESULT=" + $failure)
Write-Output ("DURATION_SECONDS=" + $properties.Duration.TotalSeconds)
Write-Output ("WIDTH=" + $properties.Width)
Write-Output ("HEIGHT=" + $properties.Height)
Write-Output ("BITRATE=" + $properties.Bitrate)
Write-Output ("VIDEO=" + $video)
