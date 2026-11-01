# Dot-sourcing only defines helpers; it never installs or starts anything.
function Invoke-NativeChecked {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $File" }
}
function Refresh-ProcessPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Get-ToolPath {
    param([string]$Name)
    if ($Name -eq 'python') {
        $candidates = @()
        $py = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($py) {
            try {
                $path = & $py.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0) { $candidates += $path }
            } catch { } # A launcher may exist without Python 3.12 installed.
        }
        $command = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($command) { $candidates += $command.Source }
        $candidates += "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
        $candidates += "$env:ProgramFiles\Python312\python.exe"
        foreach ($candidate in $candidates) {
            if ($candidate -like '*\Microsoft\WindowsApps\python.exe') { continue }
            if (Test-Path $candidate) {
                try {
                    $version = & $candidate -c "import sys; print('%s.%s' % sys.version_info[:2])" 2>$null
                    if ($LASTEXITCODE -eq 0 -and $version -eq '3.12') { return $candidate }
                } catch { } # An unusable candidate must not block automatic installation.
            }
        }
        return $null
    }
    $command = Get-Command "$Name.exe" -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $path = switch ($Name) {
        'git' { "$env:ProgramFiles\Git\cmd\git.exe" }
        'ollama' { "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
        'tesseract' { "$env:ProgramFiles\Tesseract-OCR\tesseract.exe" }
        default { throw "Unknown tool: $Name" }
    }
    if (Test-Path $path) { return $path }
    return $null
}
function Install-WindowsTool {
    param($Package)
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Install-PackageProvider -Name NuGet -Scope CurrentUser -Force | Out-Null
        Install-Module Microsoft.WinGet.Client -Repository PSGallery -Scope CurrentUser -Force
        Import-Module Microsoft.WinGet.Client
        Repair-WinGetPackageManager | Out-Host
        Refresh-ProcessPath
    }
    Invoke-NativeChecked -File 'winget.exe' -Arguments @('install', '--id', $Package.id, '--exact', '--source', 'winget', '--silent', '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity')
    Refresh-ProcessPath
}
function Ensure-WindowsTool {
    param($Package)
    $path = Get-ToolPath $Package.name
    if (-not $path) {
        Install-WindowsTool $Package
        $path = Get-ToolPath $Package.name
        if (-not $path) { throw "$($Package.name) was not found after installation. Reopen PowerShell and retry setup." }
    }
    return $path
}
function Ensure-OllamaReady {
    param([string]$File, [string]$BaseUrl)
    try { $null = Invoke-RestMethod "$BaseUrl/api/tags" -TimeoutSec 2; return } catch { }
    $process = Start-Process -FilePath $File -ArgumentList 'serve' -PassThru
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        try { $null = Invoke-RestMethod "$BaseUrl/api/tags" -TimeoutSec 2; return } catch { }
        if ($process.HasExited) { throw 'Ollama exited before becoming ready. Check port 11434 and retry.' }
    }
    throw 'Ollama did not become ready within 30 seconds. Check Ollama and retry setup.'
}

function Test-OllamaModelInstalled {
    param([string]$Name, $Installed)
    return ($Installed -contains $Name -or ($Name -notmatch ':' -and $Installed -contains "${Name}:latest"))
}
