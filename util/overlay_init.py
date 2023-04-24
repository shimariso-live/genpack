#!/usr/bin/python
import os,sys,ctypes,ctypes.util,configparser,shutil,subprocess,glob,logging,stat,fcntl
from pathlib import Path
from importlib import machinery
from inspect import signature

# libc functions
libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
libc.reboot.argtypes = (ctypes.c_int,)
RB_HALT_SYSTEM = 0xcdef0123
libc.mount.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p)
MS_MOVE = 0x2000
MS_RELATIME = (1<<21)
libc.umount.argtypes = (ctypes.c_char_p,)
libc.pivot_root.argtypes = (ctypes.c_char_p, ctypes.c_char_p)

def _exception_handler(exctype, value, traceback):
    print(value)
    rst = libc.reboot(RB_HALT_SYSTEM)

def ensure_run_mounted():
    if os.path.ismount("/run"): return
    if libc.mount(b"tmpfs", b"/run", b"tmpfs", MS_RELATIME, b"") < 0:
        raise Exception("/run counldn't be mounted")

def ensure_sys_mounted():
    if os.path.ismount("/sys"): return
    if libc.mount(b"sysfs", b"/sys", b"sysfs", 0, b"") < 0:
        raise Exception("/sys counldn't be mounted")

def ensure_proc_mounted():
    if os.path.ismount("/proc"): return
    if libc.mount(b"proc", b"/proc", b"proc", 0, b"") < 0:
        raise Exception("/proc counldn't be mounted")

def ensure_dev_mounted():
    if os.path.ismount("/dev"): return
    if libc.mount(b"udev", b"/dev", b"devtmpfs", 0, b"mode=0755,size=10M") < 0:
        raise Exception("/dev counldn't be mounted")

def mount_tmpfs(target):
    os.makedirs(target,exist_ok=True)
    if libc.mount(b"tmpfs", target.encode(), b"tmpfs", MS_RELATIME, b"") < 0:
        raise Exception("Failed to mount tmpfs on %s." % target)

def mount_overlayfs(lowerdir,upperdir,workdir,target):
    os.makedirs(upperdir,exist_ok=True)
    os.makedirs(workdir,exist_ok=True)
    os.makedirs(target,exist_ok=True)
    mountopts = "lowerdir=%s,upperdir=%s,workdir=%s" % (lowerdir, upperdir, workdir)
    if libc.mount(b"overlay", target.encode(), b"overlay", MS_RELATIME, mountopts.encode()) < 0:
        raise Exception("Overlay filesystem(%s) counldn't be mounted on %s. errno=%d" 
            % (mountopts,target,ctypes.get_errno()))

def move_mount(old, new):
    os.makedirs(new,exist_ok=True)
    if libc.mount(old.encode(), new.encode(), None, MS_MOVE, None) < 0:
        raise Exception("Moving mount point from %s to %s failed. errno=%d" % (old, new, ctypes.get_errno()))

def umount(mountpoint):
    return libc.umount(mountpoint.encode())

def coldplug_modules(root):
    CHROOT="/bin/chroot"
    MODPROBE="/sbin/modprobe"
    modaliases = set()
    for path in Path(os.path.join(root, "sys/devices")).rglob("modalias"):
        with open(path) as f:
            modaliases.add(f.read().strip())

    modprobe_query = subprocess.run([CHROOT, root, MODPROBE, "-a", "-q", "-R"] + list(modaliases), stdout=subprocess.PIPE, text=True)
    modules = set()
    for module_name in modprobe_query.stdout.strip().split():
        modules.add(module_name)

    logging.info("Loading modules: %s" % (",".join(modules)))
    subprocess.call([CHROOT, root, MODPROBE, "-a", "-b"] + list(modules))
    return True

def copytree_if_exists(srcdir, dstdir):
    if not os.path.isdir(srcdir): return False
    #else
    os.makedirs(dstdir,exist_ok=True)
    shutil.copytree(srcdir, dstdir, dirs_exist_ok=True)
    return True

def load_inifile(filename):
    parser = configparser.ConfigParser()
    if os.path.isfile(filename):
        with open(filename) as f:
            parser.read_string("[_default]\n" + f.read())
    return parser

def execute_configuration_scripts(root, ini=None):
    if ini is None: ini = configparser.ConfigParser()
    i = 0
    sys.path.append("/usr/share/overlay-init")
    for py in glob.glob("/usr/share/overlay-init/*.py"):
        try:
            mod = machinery.SourceFileLoader("_confscript%d" % i, py).load_module()
            i += 1
            if not hasattr(mod, "configure"): continue
            #else
            logging.debug("Executing configuration script %s..." % py)
            arglen = len(signature(mod.configure).parameters)
            if arglen == 2:
                mod.configure(root, ini)
            elif arglen == 1:
                mod.configure(root)
        except Exception:
            logging.exception("Exception in %s" % py)

def pivot_root(new_root, put_old):
    os.makedirs(put_old,exist_ok=True)
    if libc.pivot_root(new_root.encode(), put_old.encode()) < 0:
        raise Exception("pivot_root(%s,%s) failed. errno=%d" % (new_root,put_old,ctypes.get_errno()))

def read_qemu_fw_cfg(root):
    by_name = os.path.join(root, "sys/firmware/qemu_fw_cfg/by_name")
    ssh_public_key = os.path.join(by_name, "opt/ssh-public-key/raw")
    if os.path.isfile(ssh_public_key):
        logging.debug("SSH public key provided by VM host found")
        root_ssh = os.path.join(root, "root/.ssh")
        if os.path.isdir(root_ssh):
            authorized_keys = os.path.join(root_ssh, "authorized_keys")
            has_authorized_keys = False
            if os.path.isfile(authorized_keys):
                with open(authorized_keys) as f:
                    if f.read().strip() != "": has_authorized_keys = True
            if not has_authorized_keys:
                shutil.copyfile(ssh_public_key, authorized_keys)
                logging.info("SSH public key installed.")

    ssh_host_keys = os.path.join(by_name, "opt/ssh-host-keys/raw")
    if os.path.isfile(ssh_host_keys):
        logging.debug("SSH host key provided by VM host found")
        etc_ssh = os.path.join(root, "etc/ssh")
        if os.path.isdir(etc_ssh) and len(glob.glob(os.path.join(etc_ssh, "ssh_host_*_key"))) == 0:
            if subprocess.call(["/bin/tar", "xf", ssh_host_keys, "-k", "-C", etc_ssh]) == 0:
                logging.info("SSH host keys installed.")
            else:
                logging.error("Error installing SSH host keys.")

def is_mountable_block_device(path):
    if not os.path.exists(path): return False
    file_stat = os.stat(path)
    if not stat.S_ISBLK(file_stat.st_mode): return False
    # check if size of block device > 1MiB
    with open(path, 'rb') as device:
        BLKGETSIZE64 = 0x80081272
        size = fcntl.ioctl(device.fileno(), BLKGETSIZE64, b'\0' * 8)
        if int.from_bytes(size, sys.byteorder) < 1024*1024: return False
    return True

def is_virtiofs_provided():
    # for each /sys/bus/virtio/devices/virtio* directory(* contains number):
    # check "device" file and if it contains "0x001a" (VIRTIO_ID_FS) return True
    for path in Path("/sys/bus/virtio/devices").rglob("virtio*"):
        device_file = os.path.join(path, "device")
        if not os.path.isfile(device_file): continue
        with open(device_file) as f:
            if f.read().strip() == "0x001a": return True
    return False

def main(data_partition=None):
    RW="/run/.rw"
    BOOT="/run/.boot"
    NEWROOT="/run/.newroot"
    SHUTDOWN="/run/.shutdown"
    SHELL="/bin/sh"
    DEFAULT_DATA_PARTITION="/dev/vdb"

    has_boot_partition = os.path.ismount(BOOT)
    if not has_boot_partition and data_partition is None and is_mountable_block_device(DEFAULT_DATA_PARTITION): 
        data_partition = DEFAULT_DATA_PARTITION # in case directly invoked by kernel

    ensure_run_mounted()
    ensure_sys_mounted()
    ensure_proc_mounted()

    os.mkdir(RW)
    if data_partition is not None and is_mountable_block_device(data_partition):
        print("Trying to mount data partition %s..." % data_partition)
        if subprocess.call(["mount", data_partition, RW]) == 0:
            print("Data partition mounted.")
        else:
            print("Data partition mount failed.")

    if not os.path.ismount(RW) and is_virtiofs_provided():
        print("Trying to mount virtiofs.")
        if subprocess.call(["mount", "-t", "virtiofs", "fs", RW]) == 0:
            print("Virtiofs mounted.")
        else:
            print("Virtiofs mount failed.")
    
    if not os.path.ismount(RW):
        print("Data partition is not mounted. Proceeding with tmpfs.")
        mount_tmpfs(RW)

    overlay_upper = os.path.join(RW, "root")
    overlay_work = os.path.join(RW, "work")

    # remove ld.so.cache
    ld_so_cache = os.path.join(overlay_upper, "etc/ld.so.cache")
    if os.path.exists(ld_so_cache):
        os.remove(ld_so_cache)
        print("Removed ld.so.cache")

    # for backward compatibility
    overlay_upper_compat = os.path.join(RW, "rw/root")
    if os.path.isdir(overlay_upper_compat) and not os.path.exists(overlay_upper):
        subprocess.call(["/bin/mv", overlay_upper_compat, overlay_upper])

    # mount new root filesystem
    mount_overlayfs("/", overlay_upper, overlay_work, NEWROOT)

    inifile = load_inifile(os.path.join(BOOT, "system.ini")) if has_boot_partition else configparser.ConfigParser()
    debug = inifile.getboolean("overlay-init", "debug", fallback=False)
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s", 
        handlers=[logging.FileHandler(os.path.join(NEWROOT, "var/log/overlay-init.log"), mode='w'), logging.StreamHandler()],
        force=True)
    if debug: logging.info("Debug log enabled.")
    logging.info("Root filesystem mounted.")

    shell = inifile.get("overlay-init", "shell", fallback=None)
    if shell == "1": subprocess.call(SHELL)

    new_run = os.path.join(NEWROOT, "run")
    mount_tmpfs(new_run)
    move_mount(RW, os.path.join(new_run, "initramfs/rw"))
    if has_boot_partition:
        new_boot = os.path.join(new_run, "initramfs/boot")
        move_mount(BOOT, new_boot)

    if copytree_if_exists(SHUTDOWN, os.path.join(new_run, "initramfs")):
        logging.info("Shutdown environment is ready.")

    move_mount("/dev", os.path.join(NEWROOT, "dev"))
    move_mount("/sys", os.path.join(NEWROOT, "sys"))
    move_mount("/proc", os.path.join(NEWROOT, "proc"))

    if has_boot_partition: # no boot partition == paravirt
        logging.info("Loading device drivers...")
        coldplug_modules(NEWROOT) # invoke modprobe under newroot considering /etc/modprobe.d customization

    if os.path.isdir("/usr/share/overlay-init"):
        logging.info("Configuring system...")
        try:
            execute_configuration_scripts(NEWROOT, inifile)
        except Exception:
            logging.exception("Exception occured while configuring system")
    
    if os.path.isdir(os.path.join(NEWROOT, "sys/firmware/qemu_fw_cfg")):
        logging.info("Reading qemu_fw_cfg...")
        try:
            read_qemu_fw_cfg(NEWROOT)
        except Exception:
            logging.exception("Exception occured while reading qemu_fw_cfg")

    if shell == "2": subprocess.call(SHELL)

    logging.info("Starting actual /sbin/init...")
    os.chdir(NEWROOT)
    pivot_root(".", "run/initramfs/ro")
    os.chroot(".")
    umount("/run/initramfs/ro/run")
    os.execl("/sbin/init", "/sbin/init")

if __name__ == "__main__":
    if os.getpid() != 1: raise Exception("PID must be 1")
    data_partition = sys.argv[1] if len(sys.argv) > 1 else None
    sys.excepthook = _exception_handler
    main(data_partition)
