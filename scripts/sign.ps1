# scripts/sign.ps1 -- Authenticode-sign the given files, or skip if no cert.
#
# CODESIGN_THUMBPRINT selects a code-signing cert already present in
# Cert:\CurrentUser\My. In CI that is Openwater's SSL.com EV cert, loaded by
# eSigner CKA (the private key stays in SSL.com's cloud HSM) -- see the
# "Set up eSigner CKA" step in .github/workflows/release-build.yml and
# docs/code-signing.md. Every file signed is one metered eSigner signing.
#
# Each file is verified after signing: a signature that Windows does not
# report as Valid, that is not from the selected cert, or that carries no
# timestamp fails the script instead of shipping.
param([Parameter(Mandatory = $true)][string[]]$Files)

# signtool failures are handled through $LASTEXITCODE (with retries), not
# as terminating errors.
$PSNativeCommandUseErrorActionPreference = $false

$thumb = $env:CODESIGN_THUMBPRINT
if (-not $thumb) {
    Write-Host "signing skipped (CODESIGN_THUMBPRINT not set)" -ForegroundColor Yellow
    return
}

# signtool is not on PATH on the GitHub runners -- fall back to the newest
# Windows Kits copy.
$signtool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
if (-not $signtool) {
    $kits = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    $signtool = Get-ChildItem "$kits\10.0.*\x64\signtool.exe" -ErrorAction SilentlyContinue |
        Sort-Object { [version]$_.Directory.Parent.Name } -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $signtool) { throw "signtool.exe not found (PATH or Windows Kits)" }

foreach ($f in $Files) {
    if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { throw "file to sign not found: $f" }
    Write-Host "signing $f" -ForegroundColor Cyan
    # Both the eSigner cloud signing call and the timestamp server can flake
    # transiently, so retry before failing the build.
    $attempts = 3
    for ($i = 1; $i -le $attempts; $i++) {
        & $signtool sign /sha1 $thumb /fd SHA256 `
            /tr http://ts.ssl.com /td SHA256 $f
        if ($LASTEXITCODE -eq 0) { break }
        if ($i -eq $attempts) { throw "signtool failed for $f after $attempts attempts" }
        Write-Host "signtool exit $LASTEXITCODE -- retrying ($i/$attempts)" -ForegroundColor Yellow
        Start-Sleep -Seconds 10
    }

    $sig = Get-AuthenticodeSignature -LiteralPath $f
    if ($sig.Status -ne "Valid") {
        throw "signature on $f is $($sig.Status): $($sig.StatusMessage)"
    }
    if ($sig.SignerCertificate.Thumbprint -ne $thumb) {
        throw "signature on $f is from $($sig.SignerCertificate.Thumbprint), expected $thumb"
    }
    if (-not $sig.TimeStamperCertificate) {
        throw "signature on $f has no timestamp"
    }
    Write-Host "verified $f -- $($sig.SignerCertificate.Subject)" -ForegroundColor Green
}
