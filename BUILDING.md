# Building berserker Executable

This document provides instructions for building standalone executables of berserker using PyInstaller.

## Prerequisites

- **Python 3.8.10** (required for Windows 7 compatibility)
- **pip** package manager
- **Windows** (tested on Windows 10, compatible with Windows 7)

## Quick Build (PowerShell)

The easiest way to build is using the provided PowerShell script:

```powershell
# Build CLI executable (includes GUI support via 'berserker gui')
.\build.ps1

# Build only CLI
.\build.ps1 -CliOnly

# Build only GUI
.\build.ps1 -GuiOnly

# Build and package into ZIP
.\build.ps1 -Package

# Clean build artifacts
.\build.ps1 -Clean

# Run import tests
.\build.ps1 -Test
```

## Manual Build Steps

### Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

> **Note**: PyInstaller 5.13.2 is pinned for Windows 7 compatibility. Newer versions require Windows 8+.

### Step 2: Build the Executables

#### CLI Version (console mode)

```bash
pyinstaller berserker.spec
```

Output: `dist/berserker.exe`

#### GUI Version (windowed, no console)

```bash
pyinstaller berserker-gui.spec
```

Output: `dist/berserker-gui.exe`

### Step 3: Locate the Output

Built executables are located in the `dist/` directory:

```
dist/berserker.exe       # CLI version (console)
dist/berserker-gui.exe   # GUI version (windowed)
```

You can copy these files to any Windows 7+ machine and run them directly without requiring Python to be installed.

## Build Options Comparison

| Feature | CLI (`berserker.spec`) | GUI (`berserker-gui.spec`) |
|---------|------------------------|----------------------------|
| Console window | Yes | Yes |
| wxPython GUI | Included | Included |
| Entry point | `berserker/__main__.py` | `berserker/gui_entry.py` |
| Output name | `berserker.exe` | `berserker-gui.exe` |
| Use case | Default build - supports both CLI and GUI (`berserker gui`) | Standalone GUI-only executable |

## Notes

### Windows 7 Compatibility

- Executables are built specifically for Windows 7 compatibility
- Uses Python 3.8.10 and PyInstaller 5.13.2 to ensure broad Windows support
- Avoids newer Python features that aren't available in Python 3.8

### File Size Optimization

- UPX compression is enabled in spec files to reduce executable size
- Unnecessary modules (unittest, test frameworks, PIL) are excluded
- Only essential dependencies are included in the bundle

### Testing the Executables

After building, you can test:

```bash
# CLI version
dist/berserker.exe --version
dist/berserker.exe --help

# GUI version (opens the GUI window)
dist/berserker-gui.exe
```

### Troubleshooting

If you encounter missing module errors when running the executable:

1. Check the build log for warnings about missing imports
2. Add the missing module to the `hiddenimports` list in the appropriate `.spec` file
3. Rebuild with `pyinstaller <spec-file>`

Common issues include:
- Missing provider modules (add to hiddenimports)
- Missing tool modules (add to hiddenimports)
- Data files not being included (add to datas list)

## Development Workflow

For development, continue using the standard Python module approach:

```bash
# CLI mode
python -m berserker [command]

# GUI mode
python -m berserker.gui_entry
```

The PyInstaller build is primarily for distribution to end users who don't have Python installed.
