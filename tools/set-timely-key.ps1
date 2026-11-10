$ErrorActionPreference = 'Stop'
# Run manually in your own terminal. The secret never enters command history.
$timelySecret = Read-Host 'Timely API key (hidden input)' -AsSecureString
$timelyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($timelySecret)
try {
    $timelyValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($timelyPointer)
    if ([string]::IsNullOrWhiteSpace($timelyValue)) { throw 'Key was empty; nothing changed.' }
    [Environment]::SetEnvironmentVariable('TIMELY_API_KEY', $timelyValue.Trim(), 'User')
    Write-Host 'TIMELY_API_KEY registered. Restart the server with start.ps1.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($timelyPointer)
    $timelyValue = $null
    $timelySecret.Dispose()
}
