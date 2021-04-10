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

def run(rootfs_file, disk_image, drm=False):
    with open(disk_image, "w") as f:
        f.truncate(4 * 1024 * 1024 * 1024)
    subprocess.check_call(["parted", "--script", disk_image, "mklabel msdos", "mkpart primary 1MiB -1", "set 1 boot on", "set 1 esp on"])
    print("Run " + rootfs_file + " by qemu")
    with Loopback(disk_image) as loop:
        subprocess.check_call(sudo(["mkfs.vfat", "-F", "32", "%sp1" % loop]))
        with Tmpmount("%sp1" % loop) as mountpoint:
            grub_dir = os.path.join(mountpoint, "boot/grub")
            subprocess.check_call(sudo(["mkdir", "-p", grub_dir]))
            with Tee(os.path.join(grub_dir, "grub.cfg")) as f:
                f.write("set BOOT_PARTITION=$root\nloopback loop /system.img\nset root=loop\nset prefix=($root)/boot/grub\nnormal".encode("utf-8"))
            subprocess.check_call(sudo(["grub-install", "--target=i386-pc", "--boot-directory=%s" % os.path.join(mountpoint, "boot"), 
                "--modules=normal echo linux probe sleep test ls cat configfile cpuid minicmd vbe gfxterm_background png multiboot multiboot2 lvm xfs btrfs keystatus", loop]))
            subprocess.check_call(sudo(["cp", rootfs_file, os.path.join(mountpoint, "system.img")]))
    
    qemu_cmdline = ["qemu-system-x86_64", "-enable-kvm", "-M", "q35", "-drive", "file=%s,format=raw,index=0,media=disk,if=virtio" % disk_image,
        "-rtc", "base=utc,clock=rt", "-m", "4096", "-vga", "virtio", "-no-shutdown"]
    if drm: qemu_cmdline += ["-display", "gtk,gl=on",]
    subprocess.check_call(qemu_cmdline)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", default="./work", help="Working directory to use")
    parser.add_argument("--drm", action="store_true", default=False, help="Enable DRM(virgl)")
    parser.add_argument("rootfs", default="default-%s.squashfs" % os.uname().machine, nargs='?', help="Rootfs file to execute")
    args = parser.parse_args()
    run(args.rootfs, os.path.join(args.workdir, "qemu.img"), args.drm)
