[CmdletBinding()]
param(
    [string]$DesktopUser = 'doxagent-desktop',
    [switch]$ForgetSavedCredential,
    [switch]$NoCredentialSave
)

# One-click Singapore desktop connection.
# The script creates the existing SSH tunnel, then opens RDP on the local port.
# Credentials are stored only in Windows Credential Manager, never in this file.

$ErrorActionPreference = 'Stop'
$sshAlias = 'doxagent-sg'
$rdpHost = '127.0.0.1'
$rdpPort = 14389
$credentialTarget = 'TERMSRV/{0}:{1}' -f $rdpHost, $rdpPort
$sshProcess = $null
$startedTunnel = $false
$rdpProfile = $null

function Test-RdpTunnel {
    return $null -ne (Get-NetTCPConnection `
        -LocalAddress $rdpHost `
        -LocalPort $rdpPort `
        -State Listen `
        -ErrorAction SilentlyContinue)
}

function Wait-RdpTunnel {
    param([System.Diagnostics.Process]$Process)

    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (Test-RdpTunnel) {
            return
        }
        if ($Process.HasExited) {
            throw "SSH tunnel exited before local port $rdpPort opened (exit code $($Process.ExitCode)). Check the doxagent-sg SSH alias and network/VPN connection."
        }
        Start-Sleep -Milliseconds 500
    }

    throw "Timed out waiting for the SSH tunnel on $rdpHost`:$rdpPort."
}

function Test-SavedRdpCredential {
    $result = (& cmdkey.exe "/list:$credentialTarget" 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        return $false
    }
    # cmdkey returns exit code 0 and prints "* NONE *" when the target is absent.
    return $result -notmatch '(?im)^\s*\*\s*NONE\s*\*\s*$'
}

function Save-RdpCredential {
    param([System.Management.Automation.PSCredential]$Credential)

    $passwordPointer = [IntPtr]::Zero
    try {
        $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Credential.Password)
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
        & cmdkey.exe "/generic:$credentialTarget" "/user:$($Credential.UserName)" "/pass:$plainPassword" *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "cmdkey.exe could not save the RDP credential."
        }
    }
    finally {
        if ($passwordPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
        }
    }
}

function New-RdpProfile {
    param(
        [bool]$PromptForCredentials
    )

    $profilePath = Join-Path $env:TEMP ("doxagent-sg-{0}.rdp" -f $PID)
    $promptValue = if ($PromptForCredentials) { '1' } else { '0' }
    $desktopWidth = 1440
    $desktopHeight = 900
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $screenBounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        if ($screenBounds.Width -gt 0 -and $screenBounds.Height -gt 0) {
            $desktopWidth = $screenBounds.Width
            $desktopHeight = $screenBounds.Height
        }
    }
    catch {
        # Keep a usable windowed fallback if screen information is unavailable.
    }
    @(
        'screen mode id:i:1',
        'use multimon:i:0',
        ('desktopwidth:i:{0}' -f $desktopWidth),
        ('desktopheight:i:{0}' -f $desktopHeight),
        ('winposstr:s:0,1,0,0,{0},{1}' -f $desktopWidth, $desktopHeight),
        'smart sizing:i:1',
        'dynamic resolution:i:1',
        ('full address:s:{0}:{1}' -f $rdpHost, $rdpPort),
        ('username:s:{0}' -f $DesktopUser),
        ('prompt for credentials:i:{0}' -f $promptValue),
        'promptcredentialonce:i:0',
        'authentication level:i:2',
        'negotiate security layer:i:1',
        'gatewayusagemethod:i:4',
        'redirectclipboard:i:1',
        'autoreconnection enabled:i:1'
    ) | Set-Content -LiteralPath $profilePath -Encoding ASCII
    return $profilePath
}

try {
    if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
        throw 'ssh.exe was not found. Install/enable the Windows OpenSSH client first.'
    }
    if (-not (Get-Command mstsc.exe -ErrorAction SilentlyContinue)) {
        throw 'mstsc.exe was not found. Remote Desktop Connection is not available on this Windows installation.'
    }

    if (Test-RdpTunnel) {
        Write-Host "Reusing the existing Singapore SSH tunnel on $rdpHost`:$rdpPort."
    }
    else {
        $sshArguments = @(
            '-N',
            '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=15',
            '-o', 'ExitOnForwardFailure=yes',
            '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=3',
            '-L', "${rdpHost}:${rdpPort}:127.0.0.1:3389",
            $sshAlias
        )
        $sshProcess = Start-Process -FilePath 'ssh.exe' -ArgumentList $sshArguments -WindowStyle Hidden -PassThru
        $startedTunnel = $true
        Wait-RdpTunnel -Process $sshProcess
        Write-Host "Singapore SSH tunnel is ready on $rdpHost`:$rdpPort."
    }

    if ($ForgetSavedCredential) {
        & cmdkey.exe "/delete:$credentialTarget" *> $null
    }

    $hasSavedCredential = Test-SavedRdpCredential
    if (-not $NoCredentialSave -and -not $hasSavedCredential) {
        try {
            $credential = Get-Credential -UserName $DesktopUser -Message "Enter the Singapore RDP password. It will be saved in Windows Credential Manager."
            if ($null -ne $credential) {
                Save-RdpCredential -Credential $credential
                Write-Host "RDP credential saved for $credentialTarget."
                $hasSavedCredential = Test-SavedRdpCredential
            }
        }
        catch {
            Write-Warning "Credential was not saved: $($_.Exception.Message) RDP will open its normal login dialog."
        }
    }

    Write-Host 'Opening Singapore Remote Desktop...'
    $rdpProfile = New-RdpProfile -PromptForCredentials:(-not $hasSavedCredential)
    $mstscProcess = Start-Process -FilePath 'mstsc.exe' -ArgumentList "`"$rdpProfile`"" -PassThru
    $mstscProcess.WaitForExit()
}
catch {
    Write-Error $_
    exit 1
}
finally {
    if ($startedTunnel -and $null -ne $sshProcess -and -not $sshProcess.HasExited) {
        Stop-Process -Id $sshProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($null -ne $rdpProfile -and (Test-Path -LiteralPath $rdpProfile)) {
        Remove-Item -LiteralPath $rdpProfile -Force -ErrorAction SilentlyContinue
    }
}
