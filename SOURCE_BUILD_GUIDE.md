# Building Themes from Source

When ThemeAtlas encounters a GitHub repository or source archive that doesn't contain a pre-built release, it can build and install the theme directly.

## Automatic Detection

ThemeAtlas detects source repositories by looking for build system indicators:

- **Meson**: `meson.build` file
- **Autotools**: `configure.ac` or `configure` script
- **CMake**: `CMakeLists.txt` file

If none are found, the archive is treated as pre-packaged and installation fails.

## Usage

To build from source, use the `--allow-source-build` flag:

```bash
# Build and install from a GitHub repository
themeatlas install --archive ~/Downloads/adw-gtk3.zip --allow-source-build

# Preview what would happen (without building):
themeatlas install --archive ~/Downloads/adw-gtk3.zip --dry-run
```

### GUI

In the graphical interface, when a source build is needed, you'll see a dialog offering to:
1. Build from source (automatic, with your permission)
2. Cancel and try a different theme

## Security Measures

### Explicit Consent

Source builds are **disabled by default**. You must pass `--allow-source-build` to enable them.

### Isolated Build Environment

- Builds occur in temporary directories that are cleaned up after installation
- Only the built theme files are copied to your theme directories
- Source files and build artifacts are not retained

### Build Tool Management

Build tools (meson, autoconf, cmake) are installed only when:
1. A source build is requested
2. The tool is missing and cannot be found

### Execution Timeout

Builds timeout after:
- **Meson setup**: 15 minutes
- **Config/CMake**: 15 minutes
- **Make/build**: 30 minutes
- **Install**: 15 minutes

This prevents infinite loops or stalled builds from consuming system resources.

### Logging

All build commands and their output are logged to:
- Console output (with `--verbose` flag)
- System logs (journal with systemctl)

## Common Issues

### "Source build required; use --allow-source-build"

**What it means**: The archive contains source code, not a pre-built release.

**Solution**: 
1. Add `--allow-source-build` to allow building
2. Or download a pre-built release from the project's releases page instead

### Build fails with "meson/autoconf/cmake not found"

**What it means**: The required build tool is not installed.

**Solution**:
- ThemeAtlas can auto-install build tools if you have sudo access
- Or install manually:
  - **Ubuntu/Debian**: `sudo apt install meson cmake autoconf automake`
  - **Fedora**: `sudo dnf install meson cmake autoconf automake`
  - **Arch**: `sudo pacman -S meson cmake autoconf automake`

### Build fails with cryptic error messages

**Solution**:
1. Check if the project builds on other systems:
   ```bash
   cd extracted_archive
   meson setup build --prefix ~/.local
   meson install -C build
   ```
2. If it builds manually, report the issue to ThemeAtlas
3. If it doesn't build, check the project's issue tracker

## Best Practices

### Use Pre-Built Releases

Always prefer pre-built releases when available:

```bash
# Instead of downloading a GitHub .zip of main branch:
# https://github.com/user/theme/archive/refs/heads/main.zip

# Download a release .tar.xz instead:
# https://github.com/user/theme/releases/download/v1.0/theme-1.0.tar.xz
```

Pre-built releases are faster and have explicit version control.

### Pin Versions

When using source builds, note the version you installed:

```bash
# For future reference
echo "Installed SomeTheme from source on $(date)" >> ~/.local/share/themes/theme-notes.txt
```

### Clean Up Build Artifacts

ThemeAtlas automatically cleans up build directories. No manual cleanup needed.

### Audit Build Output

For sensitive systems, review build output:

```bash
# Run with verbose logging
themeatlas install --archive theme.tar.xz --allow-source-build --verbose

# Check system journal
journalctl -u themeatlas -n 50
```

## Technical Details

### Build Directory Structure

```
archive_extract_dir/
├── src/
│   └── (source files)
├── meson.build          (or configure.ac, CMakeLists.txt)
├── _ltm_build/          (Meson build dir - temporary)
├── _ltm_cmake_build/    (CMake build dir - temporary)
└── _ltm_prefix/         (Install prefix - temporary)
    └── share/
        ├── themes/      (GTK, shell themes)
        └── icons/       (icon theme packages)
```

Files from `_ltm_prefix/` are extracted and installed to user/system theme directories.

### Virtual Environment Isolation

Each build is isolated:
- Builds do not modify system libraries
- Builds do not persist to disk
- Only theme files are retained

### Supported Build System Features

**Meson**:
- Standard meson setup and install
- Respects meson.options
- Multi-job parallelization (-j)

**Autotools**:
- Runs autoreconf if configure missing
- Standard ./configure && make install
- Multi-job parallelization (-j)

**CMake**:
- Standard cmake && cmake --build && cmake --install
- Builds to temporary build directory
- Multi-job parallelization (-j)

## Troubleshooting

### Build hangs indefinitely

If a build seems to hang:
1. Press Ctrl+C to interrupt (CLI)
2. Close the dialog (GUI)
3. Wait for timeout (max 30 minutes)

### Insufficient disk space

Builds temporarily need space for:
1. Extracted source (varies)
2. Build artifacts (usually 2-5x source size)

Clean up:
```bash
rm -rf ~/.cache/themeatlas/
```

### Permission denied errors

If you see permission errors during build:
1. Check file permissions in the archive
2. Some projects may require root for system-wide install
3. Use `--system` cautiously; requires sudo

## Reporting Issues

If a theme fails to build, provide:

1. The theme name and source URL
2. Your OS and distribution
3. Build system type (meson/autoconf/cmake)
4. Output from a manual build attempt:
   ```bash
   cd extracted_dir
   # Try building manually and share the error
   ```

## See Also

- [README.md](README.md) - General setup and usage
- [CLI Documentation](theme_manager/cli.py) - Command-line flags
- GitHub theme project documentation - Build instructions
