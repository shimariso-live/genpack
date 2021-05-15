import os,argparse,subprocess,tempfile

from sudo import sudo,Tee

class Loopback():
    def __init__(self, backing):
        self.loop = None
        self.backing = backing
    def __enter__(self):
        self.loop = subprocess.check_output(sudo(["losetup", "-P", "-f", "--show", self.backing])).decode("utf-8").strip()
        return self.loop
    def __exit__(self, exception_type, exception_value, traceback):
        subprocess.check_call(sudo(["losetup", "-d", self.loop]))

class Tmpmount():
    def __init__(self, device):
        self.device = device
    def __enter__(self):
        self.tempdir = tempfile.TemporaryDirectory()
        subprocess.check_call(sudo(["mount", self.device, self.tempdir.name]))
        return self.tempdir.name
    def __exit__(self, exception_type, exception_value, traceback):
        subprocess.check_call(sudo(["umount", self.tempdir.name]))
        self.tempdir.cleanup()

grub_cfg = """set BOOT_PARTITION=$root
loopback loop /system.img
set root=loop
set prefix=($root)/boot/grub
if [ -f /boot/grub/grub.cfg ]; then
    normal
else
    probe -u $BOOT_PARTITION --set=BOOT_PARTITION_UUID
    linux /boot/kernel boot_partition_uuid=$BOOT_PARTITION_UUID
    initrd /boot/initramfs
    boot
fi
"""

def run(rootfs_file, disk_image, drm=False, data_volume=False, system_ini=None):
    with open(disk_image, "w") as f:
        f.truncate(8 * 1024 * 1024 * 1024 if data_volume else 4 * 1024 * 1024 * 1024)
    parted_commands = ["mklabel msdos"]
    if data_volume:
        parted_commands += ["mkpart primary 1MiB 4GiB", "mkpart primary 4GiB -1"]
    else:
        parted_commands += ["mkpart primary 1MiB -1"]
    parted_commands += ["set 1 boot on", "set 1 esp on"]
    subprocess.check_call(["parted", "--script", disk_image] + parted_commands)
    print("Run " + rootfs_file + " by qemu")
    with Loopback(disk_image) as loop:
        subprocess.check_call(sudo(["mkfs.vfat", "-F", "32", "%sp1" % loop]))
        with Tmpmount("%sp1" % loop) as mountpoint:
            grub_dir = os.path.join(mountpoint, "boot/grub")
            subprocess.check_call(sudo(["mkdir", "-p", grub_dir]))
            with Tee(os.path.join(grub_dir, "grub.cfg")) as f:
                f.write(grub_cfg.encode("utf-8"))
            subprocess.check_call(sudo(["grub-install", "--target=i386-pc", "--skip-fs-probe", "--boot-directory=%s" % os.path.join(mountpoint, "boot"), 
                "--modules=normal echo linux probe sleep test ls cat configfile cpuid minicmd vbe gfxterm_background png multiboot multiboot2 lvm xfs btrfs keystatus", loop]))
            subprocess.check_call(sudo(["cp", rootfs_file, os.path.join(mountpoint, "system.img")]))
            if system_ini: subprocess.check_call(sudo(["cp", system_ini, os.path.join(mountpoint, "system.ini")]))
        uuid = subprocess.check_output(["blkid", "-o", "value", "-s", "UUID", "%sp1" % loop]).decode("utf-8").strip()
        subprocess.check_call(sudo(["mkfs.btrfs", "-L", "data-%s" % uuid, "%sp2" % loop]))
    
    qemu_cmdline = ["qemu-system-x86_64", "-enable-kvm", "-M", "q35", "-drive", "file=%s,format=raw,index=0,media=disk,if=virtio" % disk_image,
        "-rtc", "base=utc,clock=rt", "-m", "4096", "-no-shutdown"]
    if drm: qemu_cmdline += ["-display", "gtk,gl=on", "-vga", "virtio", "-usb", "-device", "usb-tablet", "-device", "virtio-keyboard"]
    subprocess.check_call(qemu_cmdline)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", default="./work", help="Working directory to use")
    parser.add_argument("--drm", action="store_true", default=False, help="Enable DRM(virgl)")
    parser.add_argument("rootfs", default="default-%s.squashfs" % os.uname().machine, nargs='?', help="Rootfs file to execute")
    args = parser.parse_args()
    run(args.rootfs, os.path.join(args.workdir, "qemu.img"), args.drm)
