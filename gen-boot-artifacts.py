#!/usr/bin/python3

import argparse
import os
import shutil
import subprocess
import sys

RPI4_CONFIG_TXT = """\
dtoverlay=highperi
enable_uart=1
uart_2ndstage=1
initramfs initrd.cpio followkernel
"""


class Copy:
    def __init__(self, src, subdir=None):
        self.src = src

        file = os.path.basename(self.src)
        if subdir is not None:
            self.dest = os.path.join(subdir, file)
        else:
            self.dest = file

    def execute(self, *, sysroot, out):
        print(f"COPY {self.src} -> {self.dest}")
        src_abs = os.path.join(sysroot, self.src)
        dest_abs = os.path.join(out, self.dest)
        os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
        shutil.copyfile(src_abs, dest_abs)


class CopyData:
    def __init__(self, dest, data):
        self.dest = dest
        self.data = data

    def execute(self, *, sysroot, out):
        print(f"COPY_DATA {self.dest}")
        dest_abs = os.path.join(out, self.dest)
        with open(dest_abs, "w") as f:
            f.write(self.data)


class GenInitrd:
    def __init__(self, dest, *, triple):
        self.dest = dest
        self.triple = triple

    def execute(self, *, sysroot, out):
        print(f"GEN_INITRD {self.dest}")
        dest_abs = os.path.join(out, self.dest)
        os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
        args = [
            sys.executable,
            os.path.join(sysroot, "usr/managarm/bin/gen-initrd.py"),
            f"--triple={self.triple}",
            f"--sysroot={sysroot}",
            "-o",
            dest_abs,
        ]
        subprocess.run(args, check=True)


class Profile:
    def __init__(self, *, tftp):
        self.tftp = tftp


profiles = {}

profiles["raspi4"] = Profile(
    tftp=[
        Copy("usr/managarm/bin/kernel8.img"),
        Copy("usr/managarm/devicetree/bcm2711-rpi-4-b.dtb"),
        Copy("usr/managarm/devicetree/overlays/highperi.dtbo", subdir="overlays"),
        Copy("usr/lib/raspi-firmware/start4.elf"),
        Copy("usr/lib/raspi-firmware/fixup4.dat"),
        GenInitrd("initrd.cpio", triple="aarch64-managarm"),
        CopyData("config.txt", RPI4_CONFIG_TXT),
    ]
)


def gen_tftp(args):
    os.makedirs(args.out, exist_ok=True)

    profile = profiles[args.profile]
    for action in profile.tftp:
        action.execute(
            sysroot=args.sysroot,
            out=args.out,
        )


def main():
    parser = argparse.ArgumentParser(
        description="Script that generates artifacts for booting on various boards or SoCs"
    )
    parser.add_argument(
        "-p",
        "--profile",
        choices=profiles.keys(),
        required=True,
        help="Board or SoC that artifacts are generated for",
    )
    parser.add_argument("--sysroot", type=str, default="/")
    parser.add_argument("-o", "--out", type=str, required=True)
    sp = parser.add_subparsers(
        dest="cmd",
        required=True,
        help="Command to run (e.g., what artifacts to generate)",
    )

    tftp_parser = sp.add_parser(
        "tftp", help="Generate contents of tftp directory suitable for network boot"
    )
    tftp_parser  # Suppress unused variable.

    # Run the specified subcommand.
    args = parser.parse_args()

    cmd_to_fn = {
        "tftp": gen_tftp,
    }
    cmd_to_fn[args.cmd](args)


if __name__ == "__main__":
    main()
