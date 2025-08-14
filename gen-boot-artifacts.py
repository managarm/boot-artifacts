#!/usr/bin/python3

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile

# Translation table to escape strings in DTS.
DTS_TRANS = str.maketrans({'"': '\\"'})

RPI4_CONFIG_TXT = """\
enable_uart=1
uart_2ndstage=1

# Uncomment for JTAG debugging:
# gpio=22-27=np
# enable_jtag_gpio=1

initramfs initrd.cpio followkernel
"""


# Helper class to generate device tree source files (to be compiled with dtc).
# We need this to generate inputs for U-Boot's mkimage.
class DtsBuilder:
    def __init__(self, f):
        self.f = f
        self._nesting = 0

    def header(self):
        self._write_line("/dts-v1/;")

    def open_node(self, name):
        self._write_line(name + " {")
        self._nesting += 1

    def close_node(self):
        self._nesting -= 1
        self._write_line("};")

    @contextlib.contextmanager
    def node(self, name):
        self.open_node(name)
        yield
        self.close_node()

    def cells_prop(self, name, cells, *, as_hex=True):
        if as_hex:
            cell_values = [f"{c:#x}" for c in cells]
        else:
            cell_values = [str(c) for c in cells]
        self._write_line(name + " = <" + ", ".join(cell_values) + ">;")

    def string_prop(self, name, value):
        self._write_line(name + ' = "' + value.translate(DTS_TRANS) + '";')

    def incbin_prop(self, name, path):
        self._write_line(name + ' = /incbin/("' + path.translate(DTS_TRANS) + '");')

    def _write_line(self, line):
        self.f.write(" " * (4 * self._nesting) + line + "\n")


def gen_initrd(out, *, triple, sysroot):
    args = [
        sys.executable,
        os.path.join(sysroot, "usr/managarm/bin/gen-initrd.py"),
        f"--triple={triple}",
        f"--sysroot={sysroot}",
        "-o",
        out,
    ]
    subprocess.run(args, check=True)


class Copy:
    def __init__(self, src, subdir=None):
        self.src = src

        file = os.path.basename(self.src)
        if subdir is not None:
            self.dest = os.path.join(subdir, file)
        else:
            self.dest = file

    def execute(self, *, profile, sysroot, out):
        print(f"COPY {self.src} -> {self.dest}")
        src_abs = os.path.join(sysroot, self.src)
        dest_abs = os.path.join(out, self.dest)
        os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
        shutil.copyfile(src_abs, dest_abs)


class CopyData:
    def __init__(self, dest, data):
        self.dest = dest
        self.data = data

    def execute(self, *, profile, sysroot, out):
        print(f"COPY_DATA {self.dest}")
        dest_abs = os.path.join(out, self.dest)
        with open(dest_abs, "w") as f:
            f.write(self.data)


class GenInitrd:
    def __init__(self, dest):
        self.dest = dest

    def execute(self, *, profile, sysroot, out):
        print(f"GEN_INITRD {self.dest}")
        dest_abs = os.path.join(out, self.dest)

        os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
        gen_initrd(dest_abs, triple=f"{profile.arch}-managarm", sysroot=sysroot)


class FitImage:
    def __init__(self, dest, *, prekernel, dtb, load_address):
        self.dest = dest
        self.prekernel = prekernel
        self.dtb = dtb
        self.load_address = load_address

    def execute(self, *, profile, sysroot, out):
        dest_abs = os.path.join(out, self.dest)
        prekernel_abs = os.path.join(sysroot, "usr/managarm/bin/eir-virt.bin")
        dtb_abs = os.path.join(sysroot, self.dtb)

        os.makedirs(os.path.dirname(dest_abs), exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            initrd_path = os.path.join(tmp, "initrd.cpio")
            its_path = os.path.join(tmp, "image.its")

            # The .its assumes that the initrd is in the same directory.
            gen_initrd(initrd_path, triple=f"{profile.arch}-managarm", sysroot=sysroot)

            # Generate the .its
            with open(its_path, "w") as f:
                self._build_its(
                    f,
                    board=profile.name,
                    arch=profile.arch,
                    prekernel_path=prekernel_abs,
                    dtb_path=dtb_abs,
                )

            # Compile the .its to .itb using mkimage.
            args = [
                "mkimage",
                "--fit",
                its_path,
                dest_abs,
            ]
            subprocess.run(args, check=True)

    def _build_its(self, f, *, board, arch, prekernel_path, dtb_path):
        prekernel_name = os.path.basename(prekernel_path)
        dtb_name = os.path.basename(dtb_path)

        its = DtsBuilder(f)
        its.header()

        fit_arch = {
            "riscv64": "riscv",
        }.get(arch)
        if fit_arch is None:
            raise RuntimeError("Unsupported arch for FitImage()")

        with its.node("/"):
            its.string_prop("description", f"Managarm FIT image for {board}")
            its.cells_prop("#address-cells", [1], as_hex=False)

            with its.node("images"):
                with its.node(prekernel_name):
                    its.string_prop("description", "Managarm prekernel")
                    its.incbin_prop("data", os.path.realpath(prekernel_path))
                    its.string_prop("type", "kernel")
                    its.string_prop("arch", fit_arch)
                    its.string_prop("os", "linux")  # Claim to be Linux for now.
                    its.string_prop("compression", "none")
                    its.cells_prop("load", [self.load_address])
                    its.cells_prop("entry", [self.load_address])

                with its.node("initrd.cpio"):
                    its.string_prop("description", "Managarm initrd")
                    its.incbin_prop("data", "initrd.cpio")
                    its.string_prop("type", "ramdisk")
                    its.string_prop("arch", fit_arch)
                    its.string_prop("os", "linux")  # Claim to be Linux for now.
                    its.string_prop("compression", "none")

                with its.node(dtb_name):
                    its.string_prop("description", "FDT")
                    its.incbin_prop("data", os.path.realpath(dtb_path))
                    its.string_prop("type", "flat_dt")
                    its.string_prop("arch", fit_arch)
                    its.string_prop("compression", "none")

            with its.node("configurations"):
                its.string_prop("default", f"{board}")

                with its.node(f"{board}"):
                    its.string_prop("description", f"Managarm on {board}")
                    its.string_prop("kernel", prekernel_name)
                    its.string_prop("ramdisk", "initrd.cpio")
                    its.string_prop("fdt", dtb_name)


class Profile:
    def __init__(self, name, *, arch, tftp):
        self.name = name
        self.arch = arch
        self.tftp = tftp


profiles = {}

profiles["raspi4"] = Profile(
    "raspi4",
    arch="aarch64",
    tftp=[
        Copy("usr/managarm/bin/kernel8.img"),
        Copy("usr/managarm/devicetree/bcm2711-rpi-4-b.dtb"),
        Copy("usr/managarm/devicetree/overlays/highperi.dtbo", subdir="overlays"),
        Copy("usr/lib/raspi-firmware/start4.elf"),
        Copy("usr/lib/raspi-firmware/fixup4.dat"),
        GenInitrd("initrd.cpio"),
        CopyData("config.txt", RPI4_CONFIG_TXT),
        CopyData("cmdline.txt", "serial"),
    ],
)

profiles["bpi-f3"] = Profile(
    "bpi-f3",
    arch="riscv64",
    tftp=[
        FitImage(
            "bpi-f3.itb",
            prekernel="usr/managarm/bin/eir-virt.bin",
            dtb="usr/managarm/devicetree/k1-x_deb1.dtb",
            load_address=0x11000000,
        ),
    ],
)


def gen_tftp(args):
    os.makedirs(args.out, exist_ok=True)

    profile = profiles[args.profile]
    for action in profile.tftp:
        action.execute(
            profile=profile,
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
