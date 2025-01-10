import os,json,subprocess,re,shutil,glob
import workdir,arch,package,genpack_profile,genpack_json,global_options
from sudo import sudo

CONTAINER_NAME="genpack-artifact-%d" % os.getpid()

class Artifact:
    def __init__(self, artifact):
        self.name = artifact
        self.artifact_dir = os.path.join(".", "artifacts", artifact)
        self.active_variant = None
        #if not os.path.isdir(self.artifact_dir):
        #    raise Exception("No such artifact: %s" % artifact)
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
        packages = self.lookup_build_json("packages", None)
        if packages is None: packages = ['@' + self.name]
        if not isinstance(packages, list): raise Exception("packages must be list")
        #else
        return packages
    def get_dep_removals(self):
        return self.lookup_build_json("dep-removals", [])
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
    def arch_matches(self, _arch = None):
        if _arch is None: _arch = arch.get()
        arches = self.lookup_build_json("arch", None)
        if arches is None: return True
        if isinstance(arches, str): arches = [arches]
        if not isinstance(arches, list): raise Exception("arch must be string or list")
        #else
        return _arch in arches
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
        profile = self.lookup_build_json("profile", None)
        if profile is not None: return genpack_profile.Profile(profile)
        #else
        candidates = genpack_profile.Profile.get_profiles_have_set(self.name)
        if len(candidates) == 0: return genpack_profile.Profile(default_profile_name)
        if len(candidates) == 1: return candidates[0]
        #else
        candidate_names = [p.name for p in candidates]
        raise Exception("Multiple profiles found for artifact %s: %s" % (self.name, ', '.join(candidate_names)))
    def get_last_modified(self):
        if not os.path.isdir(self.artifact_dir): return 0
        # get last modified time of the artifact directory and its contents
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

def upper_exec(gentoo_dir, upper_dir, cache_dir, profile, artifact, variant, command):
    variant_args = ["-E", "VARIANT=%s" % variant] if variant is not None else []
    # convert command to list if it is string
    if isinstance(command, str): command = [command]
    subprocess.check_call(sudo(["systemd-nspawn", "-q", "--suppress-sync=true", "-M", CONTAINER_NAME, "-D", 
        gentoo_dir, "--overlay=+/:%s:/" % escape_colon(os.path.abspath(upper_dir)), 
        "--bind=%s:/var/cache" % os.path.abspath(cache_dir),
        "--bind-ro=%s:/var/db/repos/gentoo" % os.path.abspath(workdir.get_portage(False)),
        "--capability=CAP_MKNOD",
        "-E", "PROFILE=%s" % profile.name, "-E", "ARTIFACT=%s" % artifact.name] + variant_args 
        + global_options.env_as_systemd_nspawn_args()
        + command))

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

def enable_services(root_dir, services):
    if services is None: return
    if not isinstance(services, list): services = [services]
    if len(services) == 0: return
    subprocess.check_call(sudo(["systemd-nspawn", "-q", "--suppress-sync=true", "-M", CONTAINER_NAME, "-D", root_dir, "systemctl", "enable"] + services))

def get_masked_packages(gentoo_dir) -> set:
    masked_packages = set()
    genpack_mask_file = os.path.join(gentoo_dir, "etc/portage/genpack.mask")
    if not os.path.isfile(genpack_mask_file): return masked_packages
    with open(genpack_mask_file) as f:
        for line in f:
            line = re.sub(r'#.*', "", line).strip()
            if line == "": continue
            #else
            masked_packages.add(line)
    return masked_packages

def get_all_sets(gentoo_dir, pkgs, sets=None):
    if sets is None: sets = set()
    for pkg in pkgs:
        if pkg[0] == '@' and pkg not in sets:
            sets.add(pkg)
            sub_sets = []
            with open(os.path.join(gentoo_dir, "etc/portage/sets", pkg[1:])) as f:
                for line in f:
                    line = line.strip()
                    if line == "" or line[0] != '@': continue
                    #else
                    sub_sets.append(line)
            get_all_sets(gentoo_dir, sub_sets, sets)
    return list(sets)

def build(artifact):
    upper_dir = artifact.get_workdir()
    workdir.move_to_trash(upper_dir, True)
    os.makedirs(os.path.dirname(upper_dir), exist_ok=True)
    subprocess.check_call(sudo(["mkdir", upper_dir]))
    profile = artifact.get_profile()
    
    gentoo_dir = profile.get_gentoo_workdir()
    cache_dir = profile.get_cache_workdir()

    print("Copying files to artifact dir...")
    cmdline = ["/usr/bin/copyup-packages", "--bind-mount-root", "--toplevel-dirs", "--exec-package-scripts"]
    cmdline += ["--generate-metadata"]
    if artifact.is_devel(): cmdline += ["--devel"]
    for dep_removal in artifact.get_dep_removals():
        cmdline += ["--dep-removal", dep_removal]
    artifact_packages = artifact.get_packages()
    cmdline += artifact_packages
    variant = artifact.get_active_variant()
    upper_exec(gentoo_dir, upper_dir, cache_dir, profile, artifact, variant, cmdline)

    # per-package setup
    pkgs = []
    with open(os.path.join(upper_dir, ".genpack/packages")) as f:
        for line in f:
            line = line.strip()
            if line == "" or line[0] == '#': continue
            #else
            # remove trailing [...] from line
            line = re.sub(r'\[.*\]$', "", line)
            pkgs.append(line)
    # get sets from artifact_packages
    pkgs += get_all_sets(gentoo_dir, artifact_packages)

    newest_pkg_file = 0
    for pkg in pkgs:
        pkg_wo_ver = pkg if pkg[0] == '@' else package.strip_ver(pkg)
        package_dir = package.get_dir(pkg_wo_ver)
        if not os.path.isdir(package_dir): continue
        #else
        print("Processing package %s..." % pkg_wo_ver)
        newest_pkg_file = max(newest_pkg_file, sync_files(package_dir, upper_dir, r"^CONTENTS(\.|$)"))
        if os.path.isfile(os.path.join(upper_dir, "pkgbuild")):
            upper_exec(gentoo_dir, upper_dir, cache_dir, profile, artifact, variant, ["sh", "-c", "/pkgbuild && rm -f /pkgbuild"])

    # artifact specific setup
    newest_artifact_file = max(newest_pkg_file, sync_files(artifact.get_dir(), upper_dir))
    if os.path.isfile(os.path.join(upper_dir, "build")):
        print("Building artifact...")
        upper_exec(gentoo_dir, upper_dir, cache_dir, profile, artifact, variant, "/build")
    else:
        print("Artifact build script not found.")
    subprocess.check_call(sudo(["rm", "-rf", os.path.join(upper_dir, "build"), os.path.join(upper_dir,"build.json"), os.path.join(upper_dir,"usr/src")]))

    # enable services
    enable_services(upper_dir, artifact.get_services())

def pack(artifact, outfile=None, compression=None):
    if outfile is None: outfile = artifact.get_outfile()
    if compression is None: compression = artifact.get_compression()
    cmdline = ["mksquashfs", artifact.get_workdir(), outfile, "-noappend", "-no-exports"]
    if compression == "xz": cmdline += ["-comp", "xz", "-b", "1M"]
    elif compression == "gzip": cmdline += ["-Xcompression-level", "1"]
    elif compression == "lzo": cmdline += ["-comp", "lzo"]
    else: raise BaseException("Unknown compression type %s" % compression)
    cpus = global_options.cpus()
    if cpus is not None: cmdline += ["-processors", str(cpus)]
    subprocess.check_call(sudo(cmdline))
    subprocess.check_call(sudo(["chown", "%d:%d" % (os.getuid(), os.getgid()), outfile]))
