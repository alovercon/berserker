<#
.SYNOPSIS
    Build script for berserker - PowerShell script for building PyInstaller package on Windows.

.DESCRIPTION
    This script provides functions to clean, build, test, and package the berserker application
    using PyInstaller on Windows systems with PowerShell 5.1+.

.PARAMETER Clean
    Clean build artifacts (build/, dist/, __pycache__/, *.spec files)

.PARAMETER Test
    Run basic import tests

.PARAMETER Package
    Build and create ZIP archive of output

.PARAMETER CliOnly
    Build only the CLI executable (berserker-cli.exe)

.PARAMETER GuiOnly
    Build only the GUI executable (berserker-gui.exe)

.PARAMETER All
    Build both CLI and GUI executables (both with console)

.EXAMPLE
    .\build.ps1
    Builds both CLI and GUI executables using PyInstaller

.EXAMPLE
    .\build.ps1 -Clean
    Cleans all build artifacts

.EXAMPLE
    .\build.ps1 -Test
    Runs basic import tests

.EXAMPLE
    .\build.ps1 -CliOnly
    Builds only the CLI executable

.EXAMPLE
    .\build.ps1 -GuiOnly
    Builds only the GUI executable

.EXAMPLE
    .\build.ps1 -Package
    Builds both executables and packages them into a ZIP archive
#>

[CmdletBinding(DefaultParameterSetName='All')]
param(
    [Parameter(ParameterSetName='Clean')]
    [switch]$Clean,

    [Parameter(ParameterSetName='Test')]
    [switch]$Test,

    [Parameter(ParameterSetName='Package')]
    [switch]$Package,

    [Parameter(ParameterSetName='CliOnly')]
    [switch]$CliOnly,

    [Parameter(ParameterSetName='GuiOnly')]
    [switch]$GuiOnly,

    [Parameter(ParameterSetName='All')]
    [switch]$All
)

# Set error action preference to stop on any error
$ErrorActionPreference = "Stop"

# Function to check if a command exists
function Test-CommandExists {
    param([string]$Command)
    try {
        $null = Get-Command $Command -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

# Function to check prerequisites
function Test-Prerequisites {
    Write-Host "Checking prerequisites..." -ForegroundColor Cyan

    # Check Python version
    if (-not (Test-CommandExists "python")) {
        Write-Error "Python not found. Please install Python 3.8 or higher."
        return $false
    }

    $pythonVersionOutput = python --version 2>&1
    $pythonVersionMatch = $pythonVersionOutput -match 'Python (\d+)\.(\d+)\.(\d+)'
    if ($pythonVersionMatch) {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 8)) {
            Write-Error "Python version $pythonVersionOutput is too old. Requires Python 3.8+"
            return $false
        }
        Write-Host " Python version: $pythonVersionOutput" -ForegroundColor Green
    } else {
        Write-Warning "Could not determine Python version: $pythonVersionOutput"
    }

    # Check PyInstaller
    try {
        $pyinstallerVersion = python -m PyInstaller --version 2>&1
        Write-Host " PyInstaller version: $pyinstallerVersion" -ForegroundColor Green
    } catch {
        Write-Error "PyInstaller not found. Please install it with: pip install pyinstaller==5.13.2"
        return $false
    }

    Write-Host "All prerequisites met." -ForegroundColor Green
    return $true
}

# Function to clean build artifacts
function Clean-Build {
    Write-Host "Cleaning build artifacts..." -ForegroundColor Cyan

    $directoriesToRemove = @("build", "dist")

    foreach ($dir in $directoriesToRemove) {
        if (Test-Path $dir) {
            Write-Host "Removing directory: $dir" -ForegroundColor Yellow
            Remove-Item -Path $dir -Recurse -Force
        }
    }

    # Remove __pycache__ directories recursively
    $pycacheDirs = Get-ChildItem -Path . -Include "__pycache__" -Recurse -Directory
    foreach ($dir in $pycacheDirs) {
        Write-Host "Removing directory: $($dir.FullName)" -ForegroundColor Yellow
        Remove-Item -Path $dir.FullName -Recurse -Force
    }

    # Note: spec files are NOT cleaned - they are part of the project

    Write-Host "Clean completed." -ForegroundColor Green
}

# Function to build with PyInstaller
function Build-Application {
    param(
        [string]$SpecFile = "berserker.spec",
        [string]$ExpectedExe = "berserker.exe"
    )

    Write-Host "Building with PyInstaller using spec: $SpecFile..." -ForegroundColor Cyan

    if (-not (Test-Path $SpecFile)) {
        Write-Error "$SpecFile file not found. Please create it first."
        return $false
    }

    try {
        # Run PyInstaller with the spec file
        python -m PyInstaller $SpecFile --noconfirm

        if ($LASTEXITCODE -ne 0) {
            Write-Error "PyInstaller build failed with exit code $LASTEXITCODE"
            return $false
        }

        $exePath = "dist\$ExpectedExe"
        if (Test-Path $exePath) {
            Write-Host " Build successful: $exePath" -ForegroundColor Green
            return $true
        } else {
            Write-Error "Build completed but $exePath not found"
            return $false
        }
    } catch {
        Write-Error "Build failed: $_"
        return $false
    }
}

# Function to run basic tests
function Test-Application {
    Write-Host "Running basic import tests..." -ForegroundColor Cyan

    try {
        # Test importing the main module
        $testResult = python -c "import berserker; print(' berserker imported successfully')"
        Write-Host $testResult -ForegroundColor Green

        # Test CLI module
        $testResult = python -c "from berserker.cli.cli import main; print(' CLI module imported successfully')"
        Write-Host $testResult -ForegroundColor Green

        # Test GUI module (optional - may fail if wxPython not installed)
        try {
            $testResult = python -c "from berserker.gui.app import start_gui_app; print(' GUI module imported successfully')"
            Write-Host $testResult -ForegroundColor Green
        } catch {
            Write-Host " [WARN] GUI module import failed (wxPython may not be installed): $_" -ForegroundColor Yellow
        }

        Write-Host "All tests passed." -ForegroundColor Green
        return $true
    } catch {
        Write-Error "Tests failed: $_"
        return $false
    }
}

# Function to create package ZIP
function Package-Application {
    Write-Host "Creating package ZIP archive..." -ForegroundColor Cyan

    $cliExe = "dist\berserker.exe"
    $guiExe = "dist\berserker-gui.exe"

    if (-not (Test-Path $cliExe) -and -not (Test-Path $guiExe)) {
        Write-Error "No executables found in dist/. Please build first."
        return $false
    }

    try {
        # Create ZIP archive
        $zipPath = "dist\berserker.zip"
        if (Test-Path $zipPath) {
            Remove-Item $zipPath -Force
        }

        # Use PowerShell's Compress-Archive (available in PowerShell 5.0+)
        Compress-Archive -Path "dist\*" -DestinationPath $zipPath -Force

        if (Test-Path $zipPath) {
            $zipSize = (Get-Item $zipPath).Length
            Write-Host " Package created: $zipPath ($([math]::Round($zipSize / 1MB, 2)) MB)" -ForegroundColor Green
            return $true
        } else {
            Write-Error "Failed to create ZIP package"
            return $false
        }
    } catch {
        Write-Error "Packaging failed: $_"
        return $false
    }
}

# Main execution logic
try {
    # Always check prerequisites unless cleaning
    if (-not $Clean) {
        if (-not (Test-Prerequisites)) {
            exit 1
        }
    }

    # Execute based on parameter set
    switch ($PSCmdlet.ParameterSetName) {
        "Clean" {
            Clean-Build
            exit 0
        }
        "Test" {
            if (-not (Test-Application)) {
                exit 1
            }
            exit 0
        }
        "Package" {
            # Build both CLI and GUI
            if (-not (Build-Application -SpecFile "berserker.spec" -ExpectedExe "berserker.exe")) {
                exit 1
            }
            if (-not (Build-Application -SpecFile "berserker-gui.spec" -ExpectedExe "berserker-gui.exe")) {
                exit 1
            }
            if (-not (Package-Application)) {
                exit 1
            }
            exit 0
        }
        "CliOnly" {
            if (-not (Build-Application -SpecFile "berserker.spec" -ExpectedExe "berserker.exe")) {
                exit 1
            }
            exit 0
        }
        "GuiOnly" {
            if (-not (Build-Application -SpecFile "berserker-gui.spec" -ExpectedExe "berserker-gui.exe")) {
                exit 1
            }
            exit 0
        }
        default {
            # Default: Build CLI executable only (CLI supports launching GUI via 'berserker gui')
            if (-not (Build-Application -SpecFile "berserker.spec" -ExpectedExe "berserker.exe")) {
                exit 1
            }
            exit 0
        }
    }
} catch {
    Write-Error "Script execution failed: $_"
    exit 1
}
