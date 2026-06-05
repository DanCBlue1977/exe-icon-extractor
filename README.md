# EXE Icon Extractor & Injector

Extract icons from Windows executables and inject new ones — without touching the original file.

![Python](https://img.shields.io/badge/python-3.11-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

## Features

- **Extract** — pulls all icon groups from `.exe` / `.dll` files
  - Supports `RT_GROUP_ICON`, `RT_ICON` (DIB + PNG), Electron apps, PyInstaller bundles
  - Saves as multi-frame `.ico` (all sizes in one file) or individual `.png`
- **Inject** — replaces icons in any PE executable
  - Automatically resizes frames to match the original sizes
  - Rebuilds the `.rsrc` section via LIEF — no size limits
  - Saves a patched copy; original is never modified

## Quick Start

### Run from source (Miniconda)

```cmd
conda env create -f environment.yml
conda activate icon-extractor
python exe_icon_extractor.py
```

### Run pre-built executable

Download `EXEIconExtractor.exe` from [Releases](https://github.com/DanCBlue1977/exe-icon-extractor/releases) — no Python needed.

## Build from source

```cmd
conda activate icon-extractor
build.bat
```

Output: `dist\EXEIconExtractor.exe`

## Usage

### Extract tab
1. Click **Add EXE(s)** or **Folder** to load executables
2. Browse extracted icon groups — click size buttons to preview each frame
3. Save as **ICO** (all sizes in one file) or **PNG** (per size)
4. **Save All** exports every group to a folder at once

### Inject tab
1. **① Select source EXE** — previews the current icon
2. **② Select new ICO** — previews what will be injected
3. **③ Inject & Save** — choose output filename and click inject

## Dependencies

| Package | Purpose |
|---|---|
| `customtkinter` | Modern dark-mode GUI |
| `Pillow` | Image processing & ICO assembly |
| `pefile` | PE resource parsing (extraction) |
| `icoextract` | RT_GROUP_ICON extraction |
| `lief` | PE resource rebuilding (injection) |

## Technical notes

**ICO format written:**
- Frames < 256px → DIB 32bpp (BITMAPINFOHEADER + BGRA + AND mask)
- Frames ≥ 256px → PNG embedded (Vista+ standard)

**Injection strategy:**
- LIEF navigates `root → RT_ICON(id=3) → id_node → lang_node`
- Sets `lang_node.content = new_dib_bytes` for each frame
- `Builder(config.resources=True).build()` reconstructs `.rsrc` from scratch

## License

MIT
