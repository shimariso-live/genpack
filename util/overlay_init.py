#!/usr/bin/python
import os,sys,ctypes,ctypes.util,configparser,site,shutil,subprocess,glob,time,signal
from importlib import machinery
from inspect import signature

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
    print(rst)

def halt_system_on_error():
    sys.excepthook = _exception_handler

def ensure_pid_is_1():
    if os.getpid() != 1:
        raise Exception("PID must be 1")

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

def mount_fallback_to_tmpfs(source, mountpoint):
    os.makedirs(mountpoint,exist_ok=True)
    if source is not None and subprocess.call(["mount", source, mountpoint]) == 0:
        return True
    #else
    mount_tmpfs(mountpoint)
    return False

def move_mount(old, new):
    os.makedirs(new,exist_ok=True)
    if libc.mount(old.encode(), new.encode(), None, MS_MOVE, None) < 0:
        raise Exception("Moving mount point from %s to %s failed. errno=%d" % (old, new, ctypes.get_errno()))

def umount(mountpoint):
    return libc.umount(mountpoint.encode())

def start_udevd():
    pid = os.fork()
    if pid == 0:
        os._exit(os.execl("/lib/systemd/systemd-udevd", "/lib/systemd/systemd-udevd"))
    #else
    for i in range(0,3):
        if subprocess.call(["/bin/udevadm", "control", "--ping"]) == 0: break
        #else
        time.sleep(1)

    if subprocess.call(["/bin/udevadm", "trigger", "--type=all", "--action=add", 
            "--prioritized-subsystem=module,block,tpmrm,net,tty,input"]) != 0:
        print("udevadm trigger failed")
        time.sleep(1)
    return pid

def stop_udevd(pid):
    subprocess.call(["/bin/udevadm", "settle"])
    os.kill(pid, signal.SIGTERM)
    os.waitpid(pid, 0)

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
    if ini is None: ini = {}
    i = 0
    for py in glob.glob("/usr/share/overlay-init/*.py"):
        try:
            mod = machinery.SourceFileLoader("_confscript%d" % i, py).load_module()
            i += 1
            if not hasattr(mod, "configure"): continue
            #else
            arglen = len(signature(mod.configure).parameters)
            if arglen == 2:
                mod.configure(root, ini)
            elif arglen == 1:
                mod.configure(root)
        except Exception as e:
            print("py: %s" % e)
            time.sleep(3)

def pivot_root(new_root, put_old):
    os.makedirs(put_old,exist_ok=True)
    if libc.pivot_root(new_root.encode(), put_old.encode()) < 0:
        raise Exception("pivot_root(%s,%s) failed. errno=%d" % (new_root,put_old,ctypes.get_errno()))

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "install":
        shutil.copy(__file__, os.path.join(os.path.realpath(site.getsitepackages()[0]), "overlay_init.py"))
