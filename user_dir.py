import os,fcntl,logging
import arch

genpack_user_dir = os.path.expanduser('~/.genpack')

def get_genpack_user_dir():
    if not os.path.exists(genpack_user_dir):
        os.makedirs(genpack_user_dir)
    return genpack_user_dir

def get_genpack_arch_dir():
    arch_dir = os.path.join(get_genpack_user_dir(), arch.get())
    if not os.path.exists(arch_dir):
        os.makedirs(arch_dir)
    return arch_dir

def get_stage3_tarball_path(variant = "systemd-mergedusr"):
    return os.path.join(get_genpack_arch_dir(), "stage3-%s.tar.xz" % (variant,))

def get_portage_tarball_path():
    return os.path.join(get_genpack_user_dir(), "portage.tar.xz")

class lockfile:
    def __init__(self, lockfile_path):
        self.lockfile = open(lockfile_path, "a+")
        print("Waiting for lock on %s..." % lockfile_path, flush=True, end="")
        fcntl.flock(self.lockfile, fcntl.LOCK_EX)
        print("Acquired.")
    def __enter__(self):
        return self.lockfile
    def __exit__(self, type, value, traceback):
        lockfile_path = self.lockfile.name
        self.lockfile.close()
        logging.debug("Released lock on %s." % lockfile_path)

class stage3_tarball(lockfile):
    def __init__(self, variant = "systemd-mergedusr"):
        self.path = get_stage3_tarball_path(variant)
        super().__init__(self.path + ".lock")
    def __enter__(self):
        return self.path
    def __exit__(self, type, value, traceback):
        super().__exit__(type, value, traceback)

class portage_tarball(lockfile):
    def __init__(self):
        self.path = get_portage_tarball_path()
        super().__init__(self.path + ".lock")
    def __enter__(self):
        return self.path
    def __exit__(self, type, value, traceback):
        super().__exit__(type, value, traceback)

if __name__ == "__main__":
    print(get_genpack_user_dir())
    with stage3_tarball() as stage3_tarball:
        print(stage3_tarball)
    with portage_tarball() as portage_tarball:
        print(portage_tarball)
