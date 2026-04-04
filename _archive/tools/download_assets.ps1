# PowerShell script to create directory structure and download assets for Helga project

Write-Host "Starting asset download script for Helga project..."

# Define base directory
$baseDir = "D:\Projects\Tutor\helga_data"

# 1. Create directory structure
# ADDED: "$baseDir\data" (Required for kolibri_content.sqlite)
# ADDED: "$baseDir\models\small.en" (Required for Whisper STT)
Write-Host "Creating directory structure..."
$dirs = @(
    "$baseDir\data",
    "$baseDir\models",
    "$baseDir\models\piper",
    "$baseDir\models\small.en",
    "$baseDir\zim",
    "$baseDir\kuzu_db",
    "$baseDir\logs"
)

foreach ($dir in $dirs) {
    if (!(Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force
        Write-Host "Created directory: $dir"
    } else {
        Write-Host "Directory already exists: $dir"
    }
}

# Function to download file
function Download-File {
    param (
        [string]$url,
        [string]$outputPath
    )
    try {
        if (!(Test-Path $outputPath)) {
            Write-Host "Downloading $url to $outputPath..."
            Invoke-WebRequest -Uri $url -OutFile $outputPath -UseBasicParsing
            Write-Host "Downloaded: $outputPath"
        } else {
            Write-Host "Skipping (Exists): $outputPath"
        }
    } catch {
        Write-Host "CRITICAL ERROR: Failed to download $url : $_"
    }
}

# ---------------------------------------------------------
# 2. Download Qwen Model (The Teacher)
# ---------------------------------------------------------
Write-Host "Downloading Qwen Model (The Teacher)..."
$qwenUrl = "https://huggingface.co/bartowski/Qwen2.5-1B-Instruct-GGUF/resolve/main/qwen2.5-1b-instruct-q4_k_m.gguf"
$qwenPath = "$baseDir\models\qwen2.5-1b-instruct-q4_k_m.gguf"
# Force re-download by deleting existing file
if (Test-Path $qwenPath) {
    Remove-Item -Path $qwenPath -Force
    Write-Host "Deleted existing Qwen model file to force re-download."
}
Download-File -url $qwenUrl -outputPath $qwenPath

# ---------------------------------------------------------
# 3. Download Piper TTS Model (The Voice)
# ---------------------------------------------------------
Write-Host "Downloading Piper TTS Model (The Voice)..."
$piperUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
$piperPath = "$baseDir\models\piper\en_US-lessac-medium.onnx"
Download-File -url $piperUrl -outputPath $piperPath

$piperJsonUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
$piperJsonPath = "$baseDir\models\piper\en_US-lessac-medium.onnx.json"
Download-File -url $piperJsonUrl -outputPath $piperJsonPath

# ---------------------------------------------------------
# 4. Download Whisper STT Model (The Ear)
# ADDED: This section is required because Docker mounts /models as Read-Only.
# ---------------------------------------------------------
Write-Host "Downloading Whisper STT Model (The Ear)..."
$whisperBase = "https://huggingface.co/systran/faster-whisper-small.en/resolve/main"
$whisperFiles = @("config.json", "model.bin", "vocabulary.txt")

foreach ($file in $whisperFiles) {
    Download-File -url "$whisperBase/$file" -outputPath "$baseDir\models\small.en\$file"
}

# ---------------------------------------------------------
# 5. Download ZIM Files (The Library)
# ---------------------------------------------------------
Write-Host "Downloading ZIM Files (The Library)..."
$zimFiles = @(
    @{
        url = "https://download.kiwix.org/zim/speedtest/speedtest_en_blob_2024-05.zim"
        path = "$baseDir\zim\speedtest_en_blob_2024-05.zim"
    }
)

foreach ($zim in $zimFiles) {
    Download-File -url $zim.url -outputPath $zim.path
}

# ---------------------------------------------------------
# 6. Final Instructions
# ---------------------------------------------------------
Write-Host "---------------------------------------------------"
Write-Host "Asset download complete."
Write-Host "MANUAL ACTION REQUIRED:"
Write-Host "1. Locate 'kolibri_content.sqlite' from your hidden .kolibri folder."
Write-Host "2. Move it manually to: $baseDir\data\kolibri_content.sqlite"
Write-Host "---------------------------------------------------"