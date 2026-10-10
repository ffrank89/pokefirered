#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Builds the pokefirered ROM.

    python3 build.py                      # build pokefirered.gba and check it matches the original
    python3 build.py --game leafgreen     # build pokeleafgreen.gba
    python3 build.py --rev 1              # build revision 1 (or --rev switch)
    python3 build.py --no-compare         # don't check the ROM's SHA1
    python3 build.py --clean              # remove build outputs
    python3 build.py --check              # only check that the required tools are installed
    python3 build.py --install-agbcc      # download and build the agbcc compiler into tools/agbcc

All of the project's own build tools (tools/*) are Python scripts that this
runs through the Makefile. Compiling C and assembling still needs two external
toolchains, which this script checks for:

  * agbcc, the GBA C compiler used for matching builds (installed into tools/agbcc)
  * GNU binutils for ARM (arm-none-eabi-as, -ld, -objcopy, and -cpp on macOS),
    from devkitARM or a system package
"""

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))

GAMES = {"firered": "FIRERED", "leafgreen": "LEAFGREEN"}
REVISIONS = {"0": ("0", ""), "1": ("1", "_rev1"), "switch": ("10", "_switch"), "10": ("10", "_switch")}

AGBCC_REPO = "https://github.com/pret/agbcc"


def log(msg=""):
    print(msg, flush=True)


def binutils_path():
    """Returns the PATH the Makefile will use (it prepends $DEVKITARM/bin)."""
    path = os.environ.get("PATH", "")
    devkitarm = os.environ.get("DEVKITARM")
    if devkitarm and os.path.isdir(os.path.join(devkitarm, "bin")):
        path = os.path.join(devkitarm, "bin") + os.pathsep + path
    return path


def find_tool(name):
    return shutil.which(name, path=binutils_path())


def check_requirements(modern=False):
    problems = []

    if sys.version_info < (3, 8):
        problems.append("Python 3.8 or newer is required (this is %s)." % platform.python_version())

    if not shutil.which("make"):
        problems.append("`make` was not found. On macOS run `xcode-select --install`; "
                        "on Linux install the build-essential package.")

    needed = ["arm-none-eabi-as", "arm-none-eabi-ld", "arm-none-eabi-objcopy"]
    if modern:
        needed.append("arm-none-eabi-gcc")
    if modern or platform.system() == "Darwin":
        needed.append("arm-none-eabi-cpp")
    missing = [tool for tool in needed if not find_tool(tool)]
    if missing:
        hint = ("install devkitARM (see INSTALL.md), or with Homebrew: "
                "`brew install arm-none-eabi-binutils arm-none-eabi-gcc`"
                if platform.system() == "Darwin" else
                "install the binutils-arm-none-eabi package (and gcc-arm-none-eabi for modern builds), "
                "or devkitARM (see INSTALL.md)")
        problems.append("Missing ARM toolchain programs: %s\n    -> %s" % (", ".join(missing), hint))

    if not modern and not os.path.exists(os.path.join(ROOT, "tools", "agbcc", "bin", "agbcc")):
        problems.append("agbcc is not installed in tools/agbcc.\n"
                        "    -> run `python3 build.py --install-agbcc` (downloads and builds %s)" % AGBCC_REPO)

    return problems


def install_agbcc():
    if not shutil.which("git"):
        sys.exit("git is required to download agbcc.")
    workdir = tempfile.mkdtemp(prefix="agbcc-")
    src = os.path.join(workdir, "agbcc")
    try:
        log("Cloning %s ..." % AGBCC_REPO)
        subprocess.run(["git", "clone", "--depth", "1", AGBCC_REPO, src], check=True)
        log("Building agbcc (this takes a minute) ...")
        subprocess.run(["sh", "build.sh"], cwd=src, check=True)
        log("Installing into %s ..." % os.path.join(ROOT, "tools", "agbcc"))
        subprocess.run(["sh", "install.sh", ROOT], cwd=src, check=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    log("agbcc installed.")


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", choices=sorted(GAMES), default="firered")
    parser.add_argument("--rev", choices=sorted(REVISIONS), default="0", help="game revision")
    parser.add_argument("--modern", action="store_true", help="build with a modern arm-none-eabi-gcc (non-matching)")
    parser.add_argument("--no-compare", action="store_true", help="don't compare the ROM against its SHA1")
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1, help="parallel jobs")
    parser.add_argument("--clean", action="store_true", help="remove build outputs and generated files")
    parser.add_argument("--check", action="store_true", help="only check the required tools")
    parser.add_argument("--install-agbcc", action="store_true", help="download and build agbcc into tools/agbcc")
    parser.add_argument("make_args", nargs="*", help="extra arguments passed to make (e.g. KEEP_TEMPS=1)")
    args = parser.parse_args()

    os.chdir(ROOT)

    if args.install_agbcc:
        install_agbcc()
        return 0

    if args.clean:
        return subprocess.run(["make", "clean"]).returncode

    problems = check_requirements(args.modern)
    if problems:
        log("Some things the build needs are missing:\n")
        for problem in problems:
            log("  * " + problem)
        return 1
    if args.check:
        log("All build requirements are installed.")
        return 0

    game = GAMES[args.game]
    revision, suffix = REVISIONS[args.rev]
    build_name = args.game + suffix + ("_modern" if args.modern else "")
    rom = "poke%s.gba" % build_name

    make_cmd = ["make", "-j%d" % args.jobs, "GAME_VERSION=" + game, "GAME_REVISION=" + revision,
                "PYTHON=" + sys.executable]
    if args.modern:
        make_cmd.append("MODERN=1")
    make_cmd += args.make_args

    log("$ " + " ".join(make_cmd))
    result = subprocess.run(make_cmd)
    if result.returncode != 0:
        log("\nBuild failed (make exited with %d)." % result.returncode)
        return result.returncode

    log("\nBuilt %s" % os.path.join(ROOT, rom))

    sha1_file = build_name + ".sha1"
    if not args.no_compare and not args.modern and os.path.exists(sha1_file):
        with open(sha1_file) as f:
            expected = f.read().split()[0].lower()
        actual = sha1_of(rom)
        if actual == expected:
            log("%s: OK (SHA1 matches the original game)" % rom)
        else:
            log("%s: SHA1 MISMATCH\n  expected %s\n  got      %s" % (rom, expected, actual))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
