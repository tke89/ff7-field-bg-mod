# Configure + build FFNx (x86 Release) with the VS 18 Build Tools environment
$ErrorActionPreference = "Continue"
$env:PATH = "C:\Program Files (x86)\Microsoft Visual Studio\Installer;" + $env:PATH
# the FFNx-3d checkout: $env:FFNX3D, else FFNx-3d beside the Studio repo
$fork = if ($env:FFNX3D) { $env:FFNX3D } else { Join-Path $PSScriptRoot "..\..\..\FFNx-3d" }
Set-Location $fork
$vs = "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools"
cmd.exe /c "call `"$vs\VC\Auxiliary\Build\vcvarsall.bat`" x86 && set > %temp%\ffnx_vcvars.txt"
Get-Content "$env:temp\ffnx_vcvars.txt" | ForEach-Object { if ($_ -match "^(.*?)=(.*)$") { Set-Content "env:\$($matches[1])" $matches[2] } }
[Environment]::SetEnvironmentVariable('VCPKG_ROOT', '')
if (-not (Test-Path .\vcpkg\vcpkg.exe)) { cmd.exe /c "call .\vcpkg\bootstrap-vcpkg.bat -disableMetrics" }
$cmake = "$vs\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if ($args -notcontains "build-only") { & $cmake --preset Release -D_DLL_VERSION="1.24.3.9999" ; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
& $cmake --build --preset Release
exit $LASTEXITCODE
