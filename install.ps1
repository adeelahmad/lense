# Lens on Windows, in one line (PowerShell):
#
#   irm https://raw.githubusercontent.com/adeelahmad/lense/main/install.ps1 | iex
#
# or double-click "Install Lens.cmd" from a release. It makes sure Python 3 is there (with winget, else python.org's
# installer, for this user only), then opens Lens's web installer in the browser, which asks everything else and
# installs Docker Desktop and Lens (installer/install_windows.py). Settings, all optional, as environment variables:
# LENS_DIR (default: ~\lens), LENS_REPO, LENS_REF (default: main).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is much faster without its progress bar

function Show-Error($text) {
  # started from the launcher there is no console to read: say it in a window too
  Write-Host $text -ForegroundColor Red
  try { (New-Object -ComObject WScript.Shell).Popup($text, 0, 'Install Lens', 16) | Out-Null } catch {}
}

function Find-Python {
  $candidates = @(
    @('py', '-3'),
    @('python'),
    @('python3'),
    @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"),
    @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")
  )
  foreach ($c in $candidates) {
    $exe = $c[0]
    $cmd = Get-Command $exe -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    # the Microsoft Store's python.exe is a stand-in that only opens the Store
    if ($cmd.Source -like '*\WindowsApps\*') { continue }
    $rest = @($c | Select-Object -Skip 1)
    try {
      $ok = & $cmd.Source @rest -c 'import sys; print(sys.version_info >= (3, 8))' 2>$null
      if ($ok -eq 'True') { return , (@($cmd.Source) + $rest) }
    } catch {}
  }
  return $null
}

try {
  $repo = if ($env:LENS_REPO) { $env:LENS_REPO } else { 'adeelahmad/lense' }
  $ref = if ($env:LENS_REF) { $env:LENS_REF } else { 'main' }
  $dir = if ($env:LENS_DIR) { $env:LENS_DIR } else { Join-Path $HOME 'lens' }
  $base = if ($env:LENS_INSTALLER_URL) { $env:LENS_INSTALLER_URL } else { "https://raw.githubusercontent.com/$repo/$ref/installer" }

  $python = Find-Python
  if (-not $python) {
    Write-Host 'Installing Python 3 for this user...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
      winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Host
    } else {
      $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
      $setup = Join-Path $env:TEMP 'python-setup.exe'
      Invoke-WebRequest "https://www.python.org/ftp/python/3.12.7/python-3.12.7-$arch.exe" -OutFile $setup -UseBasicParsing
      Start-Process $setup -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_test=0' -Wait
    }
    $python = Find-Python
    if (-not $python) { throw 'Python 3 did not install. Install it from python.org, then try again.' }
  }

  $tmp = Join-Path $env:TEMP ('lens-installer-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $tmp | Out-Null
  foreach ($f in 'lens_installer.py', 'page.html', 'install_windows.py') {
    Invoke-WebRequest "$base/$f" -OutFile (Join-Path $tmp $f) -UseBasicParsing
  }
  Write-Host 'Opening the installer in your browser...'
  $env:LENS_INSTALLER_FETCH = '1'
  $exe = $python[0]
  $pyArgs = @($python | Select-Object -Skip 1) + @((Join-Path $tmp 'lens_installer.py'), '--dir', $dir, '--repo', $repo, '--ref', $ref)
  & $exe @pyArgs
  $code = $LASTEXITCODE
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  if ($code -ne 0) { Show-Error 'The install stopped. Open Install Lens again to see what happened and try again.' }
} catch {
  Show-Error ("Lens couldn't be installed: " + $_.Exception.Message)
}
