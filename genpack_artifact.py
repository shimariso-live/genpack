import os,json,subprocess,re
import workdir,arch,package,genpack_profile,genpack_json
from sudo import sudo

CONTAINER_NAME="genpack-artifact-%d" % os.getpid()

class Artifact:
    def __init__(self, artifact):
        self.name = artifact
        self.artifact_dir = os.path.join(".", "artifacts", artifact)
        self.active_variant = None
        if not os.path.isdir(self.artifact_dir):
            raise Exception("No such artifact: %s" % artifact)
        #else
        self.build_json = None
        build_json_path = os.path.join(self.artifact_dir, "build.json")
        if os.path.isfile(build_json_path):
            with open(build_json_path) as f:
                self.build_json = json.load(f)
    def get_dir(self):
        return self.artifact_dir
    def get_workdir(self):
        name_and_variant = self.name if self.active_variant is None else "%s:%s" % (self.name, self.active_variant)
        return workdir.get_artifact(name_and_variant, None, False)
    def lookup_build_json(self, key, default_value):
        if self.build_json is None: return default_value
        #else
        if self.active_variant is not None and "variants" in self.build_json and self.active_variant in self.build_json["variants"] and key in self.build_json["variants"][self.active_variant]:
            return self.build_json["variants"][self.active_variant][key]
        #else
        return self.build_json[key] if key in self.build_json else default_value
    def get_packages(self):
        packages = self.lookup_build_json("packages", [])
        if not isinstance(packages, list): raise Exception("packages must be list")
        #else
        return packages
    def get_files(self):
        files = self.lookup_build_json("files", [])
        if not isinstance(files, list): raise Exception("files must be list")
        #else
        return files
    def get_services(self):
        services = self.lookup_build_json("services", [])
        if not isinstance(services, list): raise Exception("services must be list")
        #else
        return services
    def is_devel(self):
        return self.lookup_build_json("devel", False)
    def get_outfile(self, default_value = None):
        if default_value is None:
            if self.active_variant is not None: default_value = "%s:%s-%s.squashfs" % (self.name, self.active_variant, arch.get())
            else: default_value = "%s-%s.squashfs" % (self.name, arch.get())
        return self.lookup_build_json("outfile", default_value)
    def get_compression(self, default_value = "gzip"):
        return self.lookup_build_json("compression", default_value)
    def get_profile(self, default_profile_name = "default"):
        return genpack_profile.Profile(self.lookup_build_json("profile", default_profile_name))
    def get_last_modified(self):
        # get last modified time of the artifact directory and its contents
        import os.path
        last_modified = os.path.getmtime(self.artifact_dir)
        for root, dirs, files in os.walk(self.artifact_dir):
            for name in files:
                last_modified = max(last_modified, os.path.getmtime(os.path.join(root, name)))
        return last_modified
    def get_build_time(self):
        packages_file = os.path.join(self.get_workdir(), ".genpack", "packages")
        if not os.path.isfile(packages_file): return None
        #else
        return os.stat(packages_file).st_mtime
    def is_up_to_date(self):
        build_date = self.get_build_time()
        if build_date is None: return False
        #else
        return build_date > max(self.get_profile().get_gentoo_workdir_time(), self.get_last_modified(), package.get_last_modified())
    def is_outfile_up_to_date(self):
        outfile = self.get_outfile()
        if not os.path.isfile(outfile): return False
        #else
        return os.path.getmtime(outfile) > self.get_build_time()
    def get_all_artifacts():
        artifact_names = genpack_json.get("artifacts", [])
        if not isinstance(artifact_names, list): raise Exception("artifacts must be list")
        if len(artifact_names) == 0:
            for i in os.listdir("./artifacts"):
                if os.path.isdir(os.path.join("./artifacts", i)): artifact_names.append(i)
        artifacts = []
        for artifact_name in artifact_names:
            artifacts.append(Artifact(artifact_name))
        return artifacts
    def set_active_variant(self, variant):
        self.active_variant = variant
    def get_active_variant(self):
        return self.active_variant

def escape_colon(s):
    # systemd-nspaws' some options need colon to be escaped
    return re.sub(r':', r'\:', s)

def copy(gentoo_dir, upper_dir, files):
    if not gentoo_dir.endswith('/'): gentoo_dir += '/'
    # files / dirs to shallow copy
    rsync = subprocess.Popen(sudo(["rsync", "-lptgoD", "--keep-dirlinks", "--files-from=-", gentoo_dir, upper_dir]), stdin=subprocess.PIPE)
    for f in files:
        if f.endswith("/."): continue
        f_wo_leading_slash = re.sub(r'^/+', "", f)
        rsync.stdin.write((f_wo_leading_slash + '\n').encode("utf-8"))
        src_path = os.path.join(gentoo_dir, f_wo_leading_slash)
        if os.path.islink(src_path):
            link = os.readlink(src_path)
            target = link[1:] if link[0] == '/' else os.path.join(os.path.dirname(f_wo_leading_slash), link)
            if os.path.exists(os.path.join(gentoo_dir, target)):
                rsync.stdin.write((target + '\n').encode("utf-8"))
    rsync.stdin.close()
    if rsync.wait() != 0: raise BaseException("rsync returned error code.")

    # dirs to deep copy
    rsync = subprocess.Popen(sudo(["rsync", "-ar", "--keep-dirlinks", "--files-from=-", gentoo_dir, upper_dir]), stdin=subprocess.PIPE)
    for f in files:
        if not f.endswith("/."): continue
        f_wo_leading_slash = re.sub(r'^/', "", f)
        rsync.stdin.write((f_wo_leading_slash + '\n').encode("utf-8"))
        src_path = os.path.join(gentoo_dir, f_wo_leading_slash)
    rsync.stdin.close()
    if rsync.wait() != 0: raise BaseException("rsync returned error code.")

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

def sync_files(srcdir, dstdir, exclude=None):
    files_to_sync, newest_file = scan_files(srcdir)

    for f in files_to_sync:
        if exclude is not None and re.match(exclude, f): continue
        src = os.path.join(srcdir, f)
        dst = os.path.join(dstdir, f)
        subprocess.check_call(sudo(["rsync", "-k", "-R", "--chown=root:root", os.path.join(srcdir, ".", f), dstdir]))
    
    return newest_file

def copyup_gcc_libs(gentoo_dir, upper_dir):
    subprocess.check_call(sudo(["systemd-nspawn", "-q", "--suppress-sync=true", "-M", CONTAINER_NAME, "-D", gentoo_dir, "--overlay=+/:%s:/" % escape_colon(os.path.abspath(upper_dir)), "sh", "-c", "touch -h `gcc --print-file-name=`/*.so.* && ldconfig" ]))

def remove_root_password(root_dir):
    subprocess.check_call(sudo(["sed", "-i", r"s/^root:\*:/root::/", os.path.join(root_dir, "etc/shadow") ]))

def make_ld_so_conf_latest(root_dir):
    subprocess.check_call(sudo(["touch", os.path.join(root_dir, "etc/ld.so.conf") ]))

def create_default_iptables_rules(root_dir):
    subprocess.check_call(sudo(["touch", os.path.join(root_dir, "var/lib/iptables/rules-save"), os.path.join(root_dir, "var/lib/ip6tables/rules-save")]))

def set_locale_conf_to_pam_env(root_dir):
    subprocess.check_call(sudo(["sed", "-i", r"s/^export LANG=\(.*\)$/#export LANG=\1 # apply \/etc\/locale.conf instead/", os.path.join(root_dir, "etc/profile.env") ]))
    subprocess.check_call(sudo(["sed", "-i", r"/^session\t\+required\t\+pam_env\.so envfile=\/etc\/profile\.env$/a session\t\trequired\tpam_env.so envfile=\/etc\/locale.conf", os.path.join(root_dir, "etc/pam.d/system-login") ]))

def enable_services(root_dir, services):
    if not isinstance(services, list): services = [services]
    subprocess.check_call(sudo(["systemd-nspawn", "-q", "--suppress-sync=true", "-M", CONTAINER_NAME, "-D", root_dir, "systemctl", "enable"] + services))

def build(artifact):
    upper_dir = artifact.get_workdir()
    workdir.move_to_trash(upper_dir, True)
    os.makedirs(os.path.dirname(upper_dir), exist_ok=True)
    subprocess.check_call(sudo(["mkdir", upper_dir]))
    profile = artifact.get_profile()

    artifact_pkgs = ["gentoo-systemd-integration", "util-linux","timezone-data","bash","gzip",
                     "grep","openssh", "coreutils", "procps", "net-tools", "iproute2", "iputils", 
                     "dbus", "python", "rsync", "tcpdump", "ca-certificates","e2fsprogs"]
    artifact_pkgs += artifact.get_packages()
    
    gentoo_dir = profile.get_gentoo_workdir()
    cache_dir = profile.get_cache_workdir()
    pkg_map = package.collect_packages(gentoo_dir)
    pkgs = package.scan_pkg_dep(gentoo_dir, pkg_map, artifact_pkgs)

    if os.path.islink(os.path.join(gentoo_dir, "bin")):
        print("System looks containing merged /usr.")
        copy(gentoo_dir, upper_dir, ["/bin", "/sbin", "/lib", "/lib64", "/usr/sbin"])

    files = package.get_all_files_of_all_packages(gentoo_dir, pkgs, artifact.is_devel())
    if os.path.isfile(os.path.join(gentoo_dir, "boot/kernel")): files.append("/boot/kernel")
    if os.path.isfile(os.path.join(gentoo_dir, "boot/initramfs")): files.append("/boot/initramfs")
    if os.path.isdir(os.path.join(gentoo_dir, "lib/modules")): files.append("/lib/modules/.")
    files += ["/dev/.", "/proc", "/sys", "/root", "/home", "/tmp", "/var/tmp", "/var/run", "/run", "/mnt"]
    files += ["/etc/passwd", "/etc/group", "/etc/shadow", "/etc/profile.env"]
    files += ["/etc/ld.so.conf", "/etc/ld.so.conf.d/."]
    files += ["/usr/lib/locale/locale-archive"]
    files += ["/bin/sh", "/bin/sed", "/usr/bin/awk", "/usr/bin/python", "/bin/nano", 
        "/bin/tar", "/usr/bin/unzip",
        "/usr/bin/wget", "/usr/bin/curl", "/usr/bin/telnet",
        "/usr/bin/make", "/usr/bin/diff", "/usr/bin/patch", "/usr/bin/strings", "/usr/bin/strace", 
        "/usr/bin/find", "/usr/bin/xargs", "/usr/bin/less"]
    files += ["/sbin/iptables", "/sbin/ip6tables", "/sbin/iptables-restore", "/sbin/ip6tables-restore", "/sbin/iptables-save", "/sbin/ip6tables-save"]
    files += ["/usr/sbin/locale-gen"]
    files += artifact.get_files()

    print("Copying files to artifact dir...")
    copy(gentoo_dir, upper_dir, files)
    copyup_gcc_libs(gentoo_dir, upper_dir)
    remove_root_password(upper_dir)
    make_ld_so_conf_latest(upper_dir)
    create_default_iptables_rules(upper_dir)
    set_locale_conf_to_pam_env(upper_dir)

    variant = artifact.get_active_variant()
    variant_args = ["-E", "VARIANT=%s" % variant] if variant is not None else []

    # per-package setup
    newest_pkg_file = 0
    for pkg in pkgs:
        pkg_wo_ver = pkg if pkg[0] == '@' else package.strip_ver(pkg)
        package_dir = package.get_dir(pkg_wo_ver)
        if not os.path.isdir(package_dir): continue
        #else
        print("Processing package %s..." % pkg_wo_ver)
        newest_pkg_file = max(newest_pkg_file, sync_files(package_dir, upper_dir, r"^CONTENTS(\.|$)"))
        if os.path.isfile(os.path.join(upper_dir, "pkgbuild")):
            subprocess.check_call(sudo(["systemd-nspawn", "-q", "--suppress-sync=true", "-M", CONTAINER_NAME, "-D", gentoo_dir, "--overlay=+/:%s:/" % escape_colon(os.path.abspath(upper_dir)), 
                "--bind=%s:/var/cache" % os.path.abspath(cache_dir),
                "-E", "PROFILE=%s" % profile.name, "-E", "ARTIFACT=%s" % artifact.name] + variant_args + [
                "--capability=CAP_MKNOD",
                "sh", "-c", "/pkgbuild && rm -f /pkgbuild" ]))

    # artifact specific setup
    newest_artifact_file = max(newest_pkg_file, sync_files(artifact.get_dir(), upper_dir))
    if os.path.isfile(os.path.join(upper_dir, "build")):
        print("Building artifact...")
        subprocess.check_call(sudo(["systemd-nspawn", "-q", "--suppress-sync=true", "-M", CONTAINER_NAME, "-D", gentoo_dir, 
            "--overlay=+/:%s:/" % escape_colon(os.path.abspath(upper_dir)), 
            "--bind=%s:/var/cache" % os.path.abspath(cache_dir),
            "-E", "PROFILE=%s" % profile.name, "-E", "ARTIFACT=%s" % artifact.name] + variant_args + [
            "/build" ]))
    else:
        print("Artifact build script not found.")
    subprocess.check_call(sudo(["rm", "-rf", os.path.join(upper_dir, "build"), os.path.join(upper_dir,"build.json"), os.path.join(upper_dir,"usr/src")]))

    # enable services
    enable_services(upper_dir, ["sshd","systemd-networkd", "systemd-resolved", "systemd-timesyncd"] + artifact.get_services())

    # generate metadata
    genpack_metadata_dir = os.path.join(upper_dir, ".genpack")
    subprocess.check_call(sudo(["mkdir", "-p", genpack_metadata_dir]))
    subprocess.check_call(sudo(["chmod", "o+rwx", genpack_metadata_dir]))
    with open(os.path.join(genpack_metadata_dir, "profile"), "w") as f:
        f.write(profile.name)
    with open(os.path.join(genpack_metadata_dir, "artifact"), "w") as f:
        f.write(artifact.name)
    if variant is not None:
        with open(os.path.join(genpack_metadata_dir, "variant"), "w") as f:
            f.write(variant)
    with open(os.path.join(genpack_metadata_dir, "packages"), "w") as f:
        for pkg in pkgs:
            if pkg[0] != '@': f.write(pkg + '\n')
    subprocess.check_call(sudo(["chown", "-R", "root:root", genpack_metadata_dir]))
    subprocess.check_call(sudo(["chmod", "755", genpack_metadata_dir]))

def pack(artifact, outfile=None, compression=None):
    if outfile is None: outfile = artifact.get_outfile()
    if compression is None: compression = artifact.get_compression()
    cmdline = ["mksquashfs", artifact.get_workdir(), outfile, "-noappend", "-no-exports"]
    if compression == "xz": cmdline += ["-comp", "xz", "-b", "1M", "-Xbcj", "x86"]
    elif compression == "gzip": cmdline += ["-Xcompression-level", "1"]
    elif compression == "lzo": cmdline += ["-comp", "lzo"]
    else: raise BaseException("Unknown compression type %s" % compression)
    subprocess.check_call(sudo(cmdline))
    subprocess.check_call(sudo(["chown", "%d:%d" % (os.getuid(), os.getgid()), outfile]))
