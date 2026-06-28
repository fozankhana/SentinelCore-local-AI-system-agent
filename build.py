#!/usr/bin/env python3
"""
Build SentinelCore distributable packages.

Usage
-----
  python build.py                      # auto-detect platform
  python build.py --platform windows
  python build.py --platform macos
  python build.py --platform linux
  python build.py --installer          # also run Inno Setup (Windows only)
  python build.py --clean              # wipe dist/ and build/ first
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

VERSION   = "1.0.0"
DIST_DIR  = Path("dist")
BUILD_DIR = Path("build")


def clean():
    for target in [DIST_DIR / "SentinelCore", BUILD_DIR]:
        if target.exists():
            shutil.rmtree(target)
            print(f"  removed {target}")


def require_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller not found.  Install: pip install pyinstaller")


def run_pyinstaller():
    print("Running PyInstaller …")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "sentinelcore.spec"],
    )
    if result.returncode != 0:
        sys.exit("PyInstaller failed.")
    out = DIST_DIR / "SentinelCore"
    print(f"  bundle: {out.resolve()}")
    return out


def build_windows_installer():
    print("Running Inno Setup …")
    candidates = [
        shutil.which("iscc"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    iscc = next((c for c in candidates if c and Path(c).exists()), None)
    if not iscc:
        print("  Inno Setup (iscc.exe) not found — skipping installer build.")
        print("  Download: https://jrsoftware.org/isdownload.php")
        return
    result = subprocess.run([iscc, "installer\\windows.iss"])
    if result.returncode == 0:
        print(f"  installer: dist/SentinelCore-{VERSION}-Setup.exe")
    else:
        print("  Inno Setup failed.")


def build_macos_app(bundle_dir: Path):
    print("Assembling macOS .app bundle …")
    app      = DIST_DIR / "SentinelCore.app"
    contents = app / "Contents"
    macos    = contents / "MacOS"
    resources = contents / "Resources"

    for d in [macos, resources]:
        d.mkdir(parents=True, exist_ok=True)

    # Copy PyInstaller output
    dest = macos / "SentinelCore"
    if bundle_dir.exists():
        shutil.copytree(bundle_dir, dest, dirs_exist_ok=True)
    else:
        sys.exit(f"PyInstaller bundle not found at {bundle_dir}")

    # Info.plist
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key>  <string>SentinelCore</string>
  <key>CFBundleExecutable</key>   <string>launcher</string>
  <key>CFBundleIdentifier</key>   <string>com.fozankhana.sentinelcore</string>
  <key>CFBundleName</key>         <string>SentinelCore</string>
  <key>CFBundleVersion</key>      <string>{VERSION}</string>
  <key>CFBundleShortVersionString</key><string>{VERSION}</string>
  <key>LSBackgroundOnly</key>     <false/>
  <key>LSUIElement</key>          <true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>"""
    (contents / "Info.plist").write_text(plist)

    # Launcher shell script
    launcher = """\
#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$DIR/SentinelCore/SentinelCore" &
SC_PID=$!
# Wait up to 10 s for the server to accept connections
for i in $(seq 1 10); do
    sleep 1
    curl -s http://localhost:4080/api/health >/dev/null 2>&1 && break
done
open http://localhost:4080
wait $SC_PID
"""
    lscript = macos / "launcher"
    lscript.write_text(launcher)
    lscript.chmod(0o755)

    print(f"  bundle: {app.resolve()}")
    print("  Drag SentinelCore.app to /Applications to install.")


def main():
    parser = argparse.ArgumentParser(description=f"Build SentinelCore v{VERSION}")
    parser.add_argument(
        "--platform",
        choices=["windows", "macos", "linux"],
        default={"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux"),
    )
    parser.add_argument("--installer", action="store_true",
                        help="Build Windows installer after PyInstaller")
    parser.add_argument("--clean",     action="store_true",
                        help="Remove dist/ and build/ before building")
    args = parser.parse_args()

    print(f"SentinelCore v{VERSION} — {args.platform} build")

    require_pyinstaller()

    if args.clean:
        clean()

    bundle = run_pyinstaller()

    if args.platform == "windows" and args.installer:
        build_windows_installer()
    elif args.platform == "macos":
        build_macos_app(bundle)

    print("\nDone.")


if __name__ == "__main__":
    main()
