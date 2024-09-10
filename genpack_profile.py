import os,subprocess,importlib.resources,glob
import workdir,user_dir,upstream,arch,genpack_json,env
import initlib,init,util
from sudo import sudo,Tee

CONTAINER_NAME="genpack-profile-%d" % os.getpid()
_extract_portage_done = False
_pull_overlay_done = False

class Profile:
    def __init__(self, profile):
        self.name = profile
        self.profile_dir = os.path.join(".", "profiles", profile)
        if not os.path.isdir(self.profile_dir):
            raise Exception("No such profile: %s" % profile)
    def __hash__(self):
        return hash(self.name)
    def __eq__(self, other):
        return isinstance(other, Profile) and self.name == other.name
    def get_dir(self):
        return self.profile_dir
    def get_workdir(self):
        return workdir.get_profile(self.name)
    def get_gentoo_workdir(self):
        return workdir.get_profile(self.name, "root")
    def get_cache_workdir(self):
        return workdir.get_profile(self.name, "cache")
    def get_gentoo_workdir_time(self):
        gentoo_dir = self.get_gentoo_workdir()
        done_file = os.path.join(gentoo_dir, ".done")
        return os.stat(done_file).st_mtime if os.path.isfile(done_file) else None
    def set_gentoo_workdir_time(self):
        gentoo_dir = self.get_gentoo_workdir()
        with open(os.path.join(gentoo_dir, ".done"), "w") as f:
            pass
    def get_all_profiles():
        profile_names = genpack_json.get("profiles", [])
        if not isinstance(profile_names, list): raise Exception("profiles must be a list")
        if len(profile_names) == 0:
            for profile_name in os.listdir(os.path.join(".", "profiles")):
                profile_names.append(profile_name)
        profiles = []
        for profile_name in profile_names:
            profiles.append(Profile(profile_name))
        return profiles
    def get_profiles_have_set(set_name):
        profiles = []
        for profile_name in os.listdir(os.path.join(".", "profiles")):
            if os.path.isfile(os.path.join(".", "profiles", profile_name, "etc/portage/sets", set_name)):
                profiles.append(Profile(profile_name))
        return profiles
    def exists(profile_name):
        return os.path.isdir(os.path.join(".", "profiles", profile_name))

def lower_exec(lower_dir, cache_dir, portage_dir, cmdline, nspawn_opts=[]):
    subprocess.check_call(sudo(
        ["systemd-nspawn", "-q", "--suppress-sync=true", "-M", CONTAINER_NAME, "-D", lower_dir, 
            "--bind=%s:/var/cache" % os.path.abspath(cache_dir),
            "--capability=CAP_MKNOD,CAP_SYS_ADMIN",
            "--bind-ro=%s:/var/db/repos/gentoo" % os.path.abspath(portage_dir) ]
            + env.get_as_systemd_nspawn_args()
            + nspawn_opts + cmdline)
    )

def put_resource_file(gentoo_dir, module, filename, dst_filename=None, make_executable=False):
    dst_path = os.path.join(gentoo_dir, dst_filename if dst_filename is not None else filename)
    with Tee(dst_path) as f:
        f.write(importlib.resources.files(module).joinpath(filename).read_bytes())
    if make_executable: subprocess.check_output(sudo(["chmod", "+x", dst_path]))

def extract_portage():
    global _extract_portage_done
    if _extract_portage_done: return
    _extract_portage_done = True
    with user_dir.portage_tarball() as portage_tarball:
        portage_dir = workdir.get_portage(True)
        upstream.download_if_necessary(upstream.get_latest_portage_tarball_url(), portage_tarball)

        # if portage is up-to-date, do nothing
        done_file = os.path.join(portage_dir, ".done")
        last_time_timestamp = 0
        if os.path.isfile(done_file):
            try:
                with open(done_file, "r") as f:
                    last_time_timestamp = float(f.read())
            except ValueError:
                os.unlink(done_file)
        tarball_timestamp = os.stat(portage_tarball).st_mtime
        if tarball_timestamp <= last_time_timestamp: return
        #else
        workdir.move_to_trash(portage_dir)

        print("Extracting portage into %s..." % portage_dir)
        os.makedirs(portage_dir)
        subprocess.check_call(sudo(["tar", "xpf", portage_tarball, "--strip-components=1", "-C", portage_dir]))
        with open(done_file, "w") as f:
            f.write(str(tarball_timestamp))

def extract_stage3(root_dir, variant = "systemd"):
    stage3_done_file = os.path.join(root_dir, ".stage3-done")
    with user_dir.stage3_tarball(variant) as stage3_tarball:
        upstream.download_if_necessary(upstream.get_latest_stage3_tarball_url(variant), stage3_tarball)
        if os.path.exists(stage3_done_file) and os.stat(stage3_done_file).st_mtime > os.stat(stage3_tarball).st_mtime:
            return

        workdir.move_to_trash(root_dir)
        os.makedirs(root_dir)
        print("Extracting stage3...")
        subprocess.check_call(sudo(["tar", "xpf", stage3_tarball, "--strip-components=1", "--exclude=./dev/*", "-C", root_dir]))

    kernel_config_dir = os.path.join(root_dir, "etc/kernels")
    repos_dir = os.path.join(root_dir, "var/db/repos/gentoo")
    subprocess.check_call(sudo(["mkdir", "-p", kernel_config_dir, repos_dir]))
    subprocess.check_call(sudo(["chmod", "-R", "o+rw", 
        os.path.join(root_dir, "etc/portage"), os.path.join(root_dir, "usr/src"), 
        os.path.join(root_dir, "var/db/repos"), os.path.join(root_dir, "var/cache"), 
        kernel_config_dir, os.path.join(root_dir, "usr/local")]))
    with open(os.path.join(root_dir, "etc/portage/make.conf"), "a") as f:
        f.write('FEATURES="-sandbox -usersandbox -network-sandbox"\n')
    with open(stage3_done_file, "w") as f:
        pass

def sync_overlay(root_dir, overlay_url = "https://github.com/wbrxcorp/genpack-overlay.git"):
    with user_dir.overlay_dir() as overlay_dir:
        global _pull_overlay_done
        if not _pull_overlay_done:
            if os.path.exists(os.path.join(overlay_dir, ".git")):
                print("Syncing genpack-overlay...")
                if subprocess.call(["git", "-C", overlay_dir, "pull"]) != 0:
                    print("Failed to pull genpack-overlay, proceeding without sync")
            else:
                print("Cloning genpack-overlay...")
                subprocess.check_call(["git", "clone", overlay_url, overlay_dir])
            _pull_overlay_done = True
        subprocess.check_call(sudo(["rsync", "-a", "--delete", overlay_dir, os.path.join(root_dir, "var/db/repos/")]))
    if not os.path.exists(os.path.join(root_dir, "etc/portage/repos.conf")):
        subprocess.check_call(sudo(["mkdir", "-m", "0777", os.path.join(root_dir, "etc/portage/repos.conf")]))
    if not os.path.isfile(os.path.join(root_dir, "etc/portage/repos.conf/genpack-overlay.conf")):
        with open(os.path.join(root_dir, "etc/portage/repos.conf/genpack-overlay.conf"), "w") as f:
            f.write("[genpack-overlay]\nlocation=/var/db/repos/genpack-overlay")

def scan_files(dir):
    files_found = []
    newest_file = 0
    for root,dirs,files in os.walk(dir, followlinks=True):
        if len(files) == 0: continue
        for f in files:
            mtime = os.stat(os.path.join(root,f)).st_mtime
            if mtime > newest_file: newest_file = mtime
            files_found.append(os.path.join(root[len(dir) + 1:], f))
    return (files_found, newest_file)

def link_files(srcdir, dstdir):
    files_to_link, newest_file = scan_files(srcdir)

    for f in files_to_link:
        src = os.path.join(srcdir, f)
        dst = os.path.join(dstdir, f)
        dst_dir = os.path.dirname(dst)
        if os.path.exists(dst_dir):
            if not os.path.isdir(dst_dir): raise Exception("%s should be a directory" % dst_dir)
        else:
            subprocess.check_call(sudo(["mkdir", "-p", dst_dir]))
        subprocess.check_call(sudo(["ln", "-f", src, dst]))
    
    return newest_file

def prepare_legacy(profile, sync = False, build_sh = True):
    if profile.name[0] == '@': raise Exception("Profile name starts with @ is reserved")
    extract_portage()
    gentoo_dir = profile.get_gentoo_workdir()
    extract_stage3(gentoo_dir)
    sync_overlay(gentoo_dir)

    common = Profile.exists("@common") and Profile("@common") or None
    newest_file = 0
    if common: newest_file = link_files(common.get_dir(), gentoo_dir)
    newest_file = max(newest_file, link_files(profile.get_dir(), gentoo_dir))
    # remove irrelevant arch dependent settings
    for i in glob.glob(os.path.join(gentoo_dir, "etc/portage/package.*/arch-*")):
        if not i.endswith("-" + arch.get()) and os.path.isfile(i): os.unlink(i)
    for i in glob.glob(os.path.join(gentoo_dir, "etc/portage/sets/*.%s" % arch.get())):
        if os.path.isfile(i): os.rename(i,  i[:i.rfind(".")])

    # move files under /var/cache
    cache_dir = profile.get_cache_workdir()
    os.makedirs(cache_dir, exist_ok=True)
    subprocess.check_call(sudo(["rsync", "-a", "--remove-source-files", os.path.join(gentoo_dir,"var/cache/"), cache_dir]))

    if os.path.isfile(os.path.join(gentoo_dir, "build.sh")):
        # put legacy resources if build.sh exists
        put_resource_file(gentoo_dir, initlib, "initlib.cpp")
        put_resource_file(gentoo_dir, initlib, "initlib.h")
        put_resource_file(gentoo_dir, initlib, "fat.cpp")
        put_resource_file(gentoo_dir, initlib, "fat.h")
        put_resource_file(gentoo_dir, init, "init.cpp")
        put_resource_file(gentoo_dir, init, "init.h")
        put_resource_file(gentoo_dir, init, "init-systemimg.cpp")
        put_resource_file(gentoo_dir, init, "init-paravirt.cpp")
        put_resource_file(gentoo_dir, util, "build-kernel.py", "usr/local/sbin/build-kernel", True)
        put_resource_file(gentoo_dir, util, "recursive-touch.py", "usr/local/bin/recursive-touch", True)
        put_resource_file(gentoo_dir, util, "overlay_init.py", "sbin/overlay-init", True)
        put_resource_file(gentoo_dir, util, "with-mysql.py", "usr/local/sbin/with-mysql", True)
        put_resource_file(gentoo_dir, util, "download.py", "usr/local/bin/download", True)
        put_resource_file(gentoo_dir, util, "install-system-image", "usr/sbin/install-system-image", True)
        put_resource_file(gentoo_dir, util, "expand-rw-layer", "usr/sbin/expand-rw-layer", True)
        put_resource_file(gentoo_dir, util, "do-with-lvm-snapshot", "usr/sbin/do-with-lvm-snapshot", True)
        put_resource_file(gentoo_dir, util, "genpack-install.cpp", "usr/src/genpack-install.cpp", True)

    portage_dir = workdir.get_portage(False)
    if sync: lower_exec(gentoo_dir, cache_dir, portage_dir, ["emerge", "--sync"])

    done_file_time = profile.get_gentoo_workdir_time()

    portage_time = os.stat(os.path.join(portage_dir, "metadata/timestamp")).st_mtime
    overlay_index = os.path.join(user_dir.get_overlay_dir(), ".git/index")
    overlay_time = os.stat(overlay_index).st_mtime if os.path.isfile(overlay_index) else 0
    newest_file = max(newest_file, portage_time, overlay_time)

    if build_sh == "force" or (build_sh == True and (not done_file_time or newest_file > done_file_time or sync)):
        lower_exec(gentoo_dir, cache_dir, portage_dir, ["emaint", "binhost", "--fix"])
        lower_exec(gentoo_dir, cache_dir, portage_dir, ["emerge", "-uDN", "-bk", "--binpkg-respect-use=y", 
            "system", "nano", "gentoolkit", "pkgdev", "zip", 
            "dev-debug/strace", "vim", "tcpdump", "netkit-telnetd"])
        prepare_script = os.path.join(gentoo_dir, "prepare")
        if os.path.isfile(prepare_script and os.access(prepare_script, os.X_OK)):
            lower_exec(gentoo_dir, cache_dir, portage_dir, ["/prepare"])
        elif os.path.isfile(os.path.join(gentoo_dir, "build.sh")):
            lower_exec(gentoo_dir, cache_dir, portage_dir, ["/build.sh"])
        else:
            print("No prepare script or build.sh found, running default commands...")
            lower_exec(gentoo_dir, cache_dir, portage_dir, ["sh", "-c", "emerge -uDN -bk --binpkg-respect-use=y genpack-progs $(ls -1 /etc/portage/sets | sed 's/^/@/')"])

        lower_exec(gentoo_dir, cache_dir, portage_dir, ["sh", "-c", "emerge -bk --binpkg-respect-use=y @preserved-rebuild && emerge --depclean && etc-update --automode -5 && eclean-dist -d && eclean-pkg -d"])
        profile.set_gentoo_workdir_time()

def prepare(profile, setup_only = False):
    extract_portage()
    gentoo_dir = profile.get_gentoo_workdir()
    extract_stage3(gentoo_dir)
    sync_overlay(gentoo_dir)

    newest_file = 0
    newest_file = max(newest_file, link_files(profile.get_dir(), gentoo_dir))

    # move files under /var/cache
    cache_dir = profile.get_cache_workdir()
    os.makedirs(cache_dir, exist_ok=True)
    subprocess.check_call(sudo(["rsync", "-a", "--remove-source-files", os.path.join(gentoo_dir,"var/cache/"), cache_dir]))

    portage_dir = workdir.get_portage(False)
    done_file_time = profile.get_gentoo_workdir_time()

    portage_time = os.stat(os.path.join(portage_dir, "metadata/timestamp")).st_mtime
    overlay_index = os.path.join(user_dir.get_overlay_dir(), ".git/index")
    overlay_time = os.stat(overlay_index).st_mtime if os.path.isfile(overlay_index) else 0
    newest_file = max(newest_file, portage_time, overlay_time)

    if setup_only or (done_file_time is not None and  newest_file <= done_file_time): return

    #else
    lower_exec(gentoo_dir, cache_dir, portage_dir, ["emerge", "-uDN", "genpack-progs"])
    lower_exec(gentoo_dir, cache_dir, portage_dir, ["genpack-prepare"])

def bash(profile):
    prepare(profile, True)
    print("Entering profile %s with bash..." % profile.name)
    try:
        lower_exec(profile.get_gentoo_workdir(), profile.get_cache_workdir(), workdir.get_portage(False), ["bash"])
    except subprocess.CalledProcessError:
        # ignore exception raised by subprocess.check_call
        pass
