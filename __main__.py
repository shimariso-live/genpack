#!/usr/bin/python3
# Copyright (c) 2021 Walbrix Corporation
# https://github.com/wbrxcorp/genpack/blob/main/LICENSE

import os,re,argparse,subprocess,glob,json
import importlib.resources
import urllib.request

import initlib,util
import qemu
from sudo import sudo,Tee

BASE_URL="http://ftp.iij.ad.jp/pub/linux/gentoo/"
CONTAINER_NAME="genpack-%d" % os.getpid()

def decode_utf8(bin):
    return bin.decode("utf-8")

def encode_utf8(str):
    return str.encode("utf-8")

def url_readlines(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as res:
        return map(decode_utf8, res.readlines())

def get_latest_stage3_tarball_url(base):
    if not base.endswith('/'): base += '/'
    for line in url_readlines(base + "releases/amd64/autobuilds/latest-stage3-amd64-systemd.txt"):
        line = re.sub(r'#.*$', "", line.strip())
        if line == "": continue
        #else
        splitted = line.split(" ")
        if len(splitted) < 2: continue
        #else
        return base + "releases/amd64/autobuilds/" + splitted[0]
    return None # not found

def get_content_length(url):
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req) as res:
        headers = res.info()
        if "Content-Length" in headers:
            return int(headers["Content-Length"])
    #else
    return None

def lower_exec(lower_dir, cache_dir, cmdline, nspawn_opts=[]):
    subprocess.check_call(sudo(["systemd-nspawn", "-q", "-M", CONTAINER_NAME, "-D", lower_dir, "--bind=%s:/var/cache" % os.path.abspath(cache_dir)] + nspawn_opts + cmdline))

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
        if not os.path.isfile(dst) or os.stat(src).st_mtime > os.stat(dst).st_mtime:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isfile(dst): os.unlink(dst)
            os.link(src, dst)
    
    return newest_file

def sync_files(srcdir, dstdir, exclude=None):
    files_to_sync, newest_file = scan_files(srcdir)

    for f in files_to_sync:
        if exclude is not None and re.match(exclude, f): continue
        src = os.path.join(srcdir, f)
        dst = os.path.join(dstdir, f)
        if not os.path.isfile(dst) or os.stat(src).st_mtime > os.stat(dst).st_mtime:
            subprocess.check_call(sudo(["rsync", "-k", "-R", "--chown=root:root", os.path.join(srcdir, ".", f), dstdir]))
    
    return newest_file

def get_newest_mtime(srcdir):
    return scan_files(srcdir)[1]

def put_resource_file(gentoo_dir, module, filename, dst_filename=None, make_executable=False):
    dst_path = os.path.join(gentoo_dir, dst_filename if dst_filename is not None else filename)
    with Tee(dst_path) as f:
        f.write(importlib.resources.read_binary(module, filename))
    if make_executable: subprocess.check_output(sudo(["chmod", "+x", dst_path]))

def load_json_file(path):
    if not os.path.isfile(path): return None
    #else
    with open(path) as f:
        return json.load(f)

def main(base, workdir, arch, sync, bash, artifact, outfile=None, profile=None):
    artifact_dir = os.path.join(".", "artifacts", artifact)
    build_json = load_json_file(os.path.join(artifact_dir, "build.json"))

    if profile is None:
        profile = "default"
        if build_json and "profile" in build_json: profile = build_json["profile"]

    stage3_tarball_url = get_latest_stage3_tarball_url(base)
    portage_tarball_url = base + "snapshots/portage-latest.tar.xz"

    arch_workdir = os.path.join(workdir, arch)
    os.makedirs(arch_workdir, exist_ok=True)

    stage3_tarball = os.path.join(arch_workdir, "stage3.tar.xz")
    portage_tarball = os.path.join(workdir, "portage.tar.xz")

    gentoo_dir = os.path.join(arch_workdir, "profiles", profile, "root")
    repos_dir = os.path.join(gentoo_dir, "var/db/repos/gentoo")
    usr_local_dir = os.path.join(gentoo_dir, "usr/local")

    if not os.path.isfile(stage3_tarball) or os.path.getsize(stage3_tarball) != get_content_length(stage3_tarball_url):
        subprocess.check_call(["wget", "-O", stage3_tarball, stage3_tarball_url])
    
    if not os.path.isfile(portage_tarball) or os.path.getsize(portage_tarball) != get_content_length(portage_tarball_url):
        subprocess.check_call(["wget", "-O", portage_tarball, portage_tarball_url])

    stage3_done_file = os.path.join(gentoo_dir, ".stage3-done")
    stage3_done_file_time = os.stat(stage3_done_file).st_mtime if os.path.isfile(stage3_done_file) else None
    if not stage3_done_file_time or stage3_done_file_time < os.stat(stage3_tarball).st_mtime:
        if os.path.isdir(gentoo_dir):
            print("Cleaning up existing gentoo tree...")
            subprocess.check_call(sudo(["rm", "-rf", gentoo_dir]))
        os.makedirs(repos_dir, exist_ok=True)
        print("Extracting stage3...")
        subprocess.check_call(sudo(["tar", "xpf", stage3_tarball, "--strip-components=1", "-C", gentoo_dir]))
        print("Extracting portage...")
        subprocess.check_call(sudo(["tar", "xpf", portage_tarball, "--strip-components=1", "-C", repos_dir]))
        kernel_config_dir = os.path.join(gentoo_dir, "etc/kernels")
        subprocess.check_call(sudo(["mkdir", "-p", kernel_config_dir]))
        subprocess.check_call(sudo(["chmod", "-R", "o+rw", os.path.join(gentoo_dir, "etc/portage"), os.path.join(gentoo_dir, "usr/src"), os.path.join(gentoo_dir, "var/db/repos"), kernel_config_dir, usr_local_dir]))
        with open(os.path.join(gentoo_dir, "etc/portage/make.conf"), "a") as f:
            f.write('FEATURES="-sandbox -usersandbox -network-sandbox"\n')
        with open(stage3_done_file, "w") as f:
            pass

    newest_file = link_files(os.path.join(".", "profiles", profile), gentoo_dir)
    put_resource_file(gentoo_dir, initlib, "initlib.cpp")
    put_resource_file(gentoo_dir, initlib, "initlib.h")
    put_resource_file(gentoo_dir, util, "build-kernel.py", "usr/local/sbin/build-kernel", True)
    put_resource_file(gentoo_dir, util, "with-mysql.py", "usr/local/sbin/with-mysql", True)
    put_resource_file(gentoo_dir, util, "install-system-image", "usr/sbin/install-system-image", True)
    put_resource_file(gentoo_dir, util, "expand-rw-layer", "usr/sbin/expand-rw-layer", True)
    put_resource_file(gentoo_dir, util, "do-with-lvm-snapshot", "usr/sbin/do-with-lvm-snapshot", True)
    put_resource_file(gentoo_dir, util, "rpmbootstrap.py", "usr/sbin/rpmbootstrap", True)

    cache_dir = os.path.join(arch_workdir, "profiles", profile, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    if sync: lower_exec(gentoo_dir, cache_dir, ["emerge", "--sync"])
    if bash: 
        print("Entering shell... 'exit 1' to abort the process.")
        lower_exec(gentoo_dir, cache_dir, ["bash"])

    done_file = os.path.join(gentoo_dir, ".done")
    done_file_time = os.stat(done_file).st_mtime if os.path.isfile(done_file) else None

    portage_time = os.stat(os.path.join(repos_dir, "metadata/timestamp")).st_mtime
    newest_file = max(newest_file, portage_time)

    if (not done_file_time or newest_file > done_file_time or sync or artifact == "none"):
        lower_exec(gentoo_dir, cache_dir, ["emerge", "-uDN", "-bk", "--binpkg-respect-use=y", 
            "system", "nano", "gentoolkit", "repoman", 
            "strace", "vim", "tcpdump", "netkit-telnetd"])
        if os.path.isfile(os.path.join(gentoo_dir, "build.sh")):
            lower_exec(gentoo_dir, cache_dir, ["/build.sh"])
        lower_exec(gentoo_dir, cache_dir, ["sh", "-c", "emerge -bk --binpkg-respect-use=y @preserved-rebuild && emerge --depclean && eselect python update && eselect python cleanup && etc-update --automode -5 && eclean-dist -d && eclean-pkg -d"])
        with open(done_file, "w") as f:
            pass
    
    if artifact == "none": return None # no build artifact
    elif artifact == "bash": 
        lower_exec(gentoo_dir, cache_dir, ["bash"])
        return None
    #else

    ##### building profile done
    ##### build artifact if necessary
    upper_dir = os.path.join(arch_workdir, "artifacts", artifact)
    genpack_metadata_dir = os.path.join(upper_dir, ".genpack")
    if not os.path.exists(genpack_metadata_dir) or os.stat(genpack_metadata_dir).st_mtime < max(os.stat(done_file).st_mtime, get_newest_mtime(artifact_dir), get_newest_mtime(os.path.join(".", "packages"))):
        build_artifact(profile, artifact, gentoo_dir, cache_dir, upper_dir, build_json)

    # final output
    if outfile is None:
        if build_json and "outfile" in build_json: outfile = build_json["outfile"]
        else: outfile = "%s-%s.squashfs" % (artifact, arch)

    if outfile == "-":
        subprocess.check_call(sudo(["systemd-nspawn", "-M", CONTAINER_NAME, "-q", "-D", upper_dir, "--network-veth", "-b"]))
        return None
    #else
    if not os.path.isfile(outfile) or os.stat(genpack_metadata_dir).st_mtime > os.stat(outfile).st_mtime:
        pack(upper_dir, outfile)
    return outfile

def build_artifact(profile, artifact, gentoo_dir, cache_dir, upper_dir, build_json):
    artifact_pkgs = ["gentoo-systemd-integration", "util-linux","timezone-data","bash","openssh", "coreutils", "procps", "net-tools", 
        "iproute2", "iputils", "dbus", "python"]
    if build_json and "packages" in build_json:
        if not isinstance(build_json["packages"], list): raise Exception("packages must be list")
        #else
        artifact_pkgs += build_json["packages"]

    pkg_map = collect_packages(gentoo_dir)
    pkgs = scan_pkg_dep(gentoo_dir, pkg_map, artifact_pkgs)
    packages_dir = os.path.join(".", "packages")
    files = process_pkgs(gentoo_dir, packages_dir, pkgs)
    if os.path.isfile(os.path.join(gentoo_dir, "boot/kernel")): files.append("/boot/kernel")
    if os.path.isfile(os.path.join(gentoo_dir, "boot/initramfs")): files.append("/boot/initramfs")
    if os.path.isdir(os.path.join(gentoo_dir, "lib/modules")): files.append("/lib/modules/.")
    files += ["/dev/.", "/proc", "/sys", "/root", "/home", "/tmp", "/var/tmp", "/var/run", "/run", "/mnt"]
    files += ["/etc/passwd", "/etc/group", "/etc/shadow", "/etc/profile.env"]
    files += ["/etc/ld.so.conf", "/etc/ld.so.conf.d/."]
    files += ["/usr/lib/locale/locale-archive"]
    files += ["/bin/sh", "/bin/sed", "/usr/bin/awk", "/usr/bin/python", "/usr/bin/vi", "/usr/bin/nano", 
        "/usr/bin/wget", "/usr/bin/curl", "/usr/bin/rsync", "/usr/sbin/tcpdump", "/usr/bin/telnet",
        "/usr/bin/make", "/usr/bin/diff", "/usr/bin/strings", "/usr/bin/strace", 
        "/usr/bin/find", "/usr/bin/xargs", "/usr/bin/less"]
    files += ["/sbin/iptables", "/sbin/ip6tables", "/sbin/iptables-restore", "/sbin/ip6tables-restore", "/sbin/iptables-save", "/sbin/ip6tables-save"]

    if build_json and "files" in build_json:
        if not isinstance(build_json["files"], list): raise Exception("files must be list")
        #else
        files += build_json["files"]

    if os.path.isdir(upper_dir):
        print("Deleting previous artifact dir...")
        subprocess.check_call(sudo(["rm", "-rf", upper_dir]))
    os.makedirs(os.path.dirname(upper_dir), exist_ok=True)
    subprocess.check_call(sudo(["mkdir", upper_dir]))
    print("Copying files to artifact dir...")
    copy(gentoo_dir, upper_dir, files)
    copyup_gcc_libs(gentoo_dir, upper_dir)
    remove_root_password(upper_dir)
    make_ld_so_conf_latest(upper_dir)
    create_default_iptables_rules(upper_dir)

    # per-package setup
    newest_pkg_file = 0
    for pkg in pkgs:
        pkg_wo_ver = strip_ver(pkg)
        package_dir = os.path.join(packages_dir, pkg_wo_ver)
        if not os.path.isdir(package_dir): continue
        #else
        print("Processing package %s..." % pkg_wo_ver)
        newest_pkg_file = max(newest_pkg_file, sync_files(package_dir, upper_dir, r"^CONTENTS(\.|$)"))
        if os.path.isfile(os.path.join(upper_dir, "pkgbuild")):
            subprocess.check_call(sudo(["systemd-nspawn", "-q", "-M", CONTAINER_NAME, "-D", gentoo_dir, "--overlay=+/:%s:/" % os.path.abspath(upper_dir), 
                "-E", "PROFILE=%s" % profile, "-E", "ARTIFACT=%s" % artifact, 
                "sh", "-c", "/pkgbuild && rm -f /pkgbuild" ]))

    # enable services
    services = ["sshd","systemd-networkd", "systemd-resolved"]
    if build_json and "services" in build_json:
        if not isinstance(build_json["services"], list): raise Exception("services must be list")
        #else
        services += build_json["services"]
    enable_services(upper_dir, services)

    # artifact specific setup
    artifact_dir = os.path.join(".", "artifacts", artifact)
    newest_artifact_file = max(newest_pkg_file, sync_files(artifact_dir, upper_dir))
    if os.path.isfile(os.path.join(upper_dir, "build")):
        print("Building artifact...")
        subprocess.check_call(sudo(["systemd-nspawn", "-q", "-M", CONTAINER_NAME, "-D", gentoo_dir, 
            "--overlay=+/:%s:/" % os.path.abspath(upper_dir), 
            "--bind=%s:/var/cache" % os.path.abspath(cache_dir),
            "/build" ]))
    else:
        print("Artifact build script not found.")
    subprocess.check_call(sudo(["rm", "-rf", os.path.join(upper_dir, "build"), os.path.join(upper_dir,"build.json"), os.path.join(upper_dir,"usr/src")]))

    # generate metadata
    # TODO: use tee
    genpack_metadata_dir = os.path.join(upper_dir, ".genpack")
    subprocess.check_call(sudo(["mkdir", genpack_metadata_dir]))
    subprocess.check_call(sudo(["chmod", "o+rwx", genpack_metadata_dir]))
    with open(os.path.join(genpack_metadata_dir, "profile"), "w") as f:
        f.write(profile)
    with open(os.path.join(genpack_metadata_dir, "artifact"), "w") as f:
        f.write(artifact)
    with open(os.path.join(genpack_metadata_dir, "packages"), "w") as f:
        for pkg in pkgs:
            f.write(pkg + '\n')
    subprocess.check_call(sudo(["chown", "-R", "root.root", genpack_metadata_dir]))
    subprocess.check_call(sudo(["chmod", "755", genpack_metadata_dir]))

def strip_ver(pkgname):
    pkgname = re.sub(r'-r[0-9]+?$', "", pkgname) # remove rev part
    last_dash = pkgname.rfind('-')
    if last_dash < 0: return pkgname
    next_to_dash = pkgname[last_dash + 1]
    return pkgname[:last_dash] if pkgname.find('/') < last_dash and (next_to_dash >= '0' and next_to_dash <= '9') else pkgname

def collect_packages(gentoo_dir):
    pkg_map = {}
    db_dir = os.path.join(gentoo_dir, "var/db/pkg")
    for category in os.listdir(db_dir):
        cat_dir = os.path.join(db_dir, category)
        if not os.path.isdir(cat_dir): continue
        #else
        for pn in os.listdir(cat_dir):
            pkg_dir = os.path.join(cat_dir, pn)
            if not os.path.isdir(pkg_dir): continue
            #else
            cat_pn = "%s/%s" % (category, pn)
            pn_wo_ver = strip_ver(pn)
            cat_pn_wo_ver = "%s/%s" % (category, pn_wo_ver)
            if pn_wo_ver in pkg_map: pkg_map[pn_wo_ver].append(cat_pn)
            else: pkg_map[pn_wo_ver] = [cat_pn]
            if cat_pn_wo_ver in pkg_map: pkg_map[cat_pn_wo_ver].append(cat_pn)
            else: pkg_map[cat_pn_wo_ver] = [cat_pn]
    return pkg_map

def get_package_set(gentoo_dir, set_name):
    pkgs = []
    with open(os.path.join(gentoo_dir, "etc/portage/sets", set_name)) as f:
        for line in f:
            line = re.sub(r'#.*', "", line).strip()
            if line != "": pkgs.append(line)
    return pkgs

def split_rdepend(line):
    if line.startswith("|| ( "):
        idx = 5
        level = 0
        while idx < len(line):
            ch = line[idx]
            if ch == '(': level += 1
            elif ch == ')':
                if level == 0:
                    idx += 1
                    break
                else: level -= 1
            idx += 1
        leftover = line[idx:].strip()
        return (line[:idx], None if leftover == "" else leftover)

    #else:
    splitted = line.split(' ', 1)
    if len(splitted) == 1: return (splitted[0],None)
    #else
    return (splitted[0], splitted[1])

def parse_rdepend_line(line, make_optional=False):
    p = []
    while line is not None and line.strip() != "":
        splitted = split_rdepend(line)
        p.append(splitted[0])
        line = splitted[1]

    pkgs = set()
    for pkg in p:
        m = re.match(r"\|\| \( (.+) \)", pkg)
        if m:
            pkgs |= parse_rdepend_line(m.group(1), True)
            continue
        if pkg[0] == '!': continue
        if pkg[0] == '~': pkg = pkg[1:]
        #else
        pkg_stripped = strip_ver(re.sub(r':.+$', "", re.sub(r'\[.+\]$', "", re.sub(r'^(<=|>=|=|<|>)', "", pkg))))
        pkgs.add('?' + pkg_stripped if make_optional else pkg_stripped)
    return pkgs

def scan_pkg_dep(gentoo_dir, pkg_map, pkgnames, pkgs = None):
    if pkgs is None: pkgs = set()
    for pkgname in pkgnames:
        if pkgname[0] == '@':
            scan_pkg_dep(gentoo_dir, pkg_map, get_package_set(gentoo_dir, pkgname[1:]), pkgs)
            continue
        optional = False
        if pkgname[0] == '?': 
            optional = True
            pkgname = pkgname[1:]
        if pkgname not in pkg_map:
            if optional: continue
            else: raise BaseException("Package %s not found" % pkgname)
        if len(pkg_map[pkgname]) > 1: raise BaseException("Package %s is ambigious" % pkgname)
        cat_pn = pkg_map[pkgname][0]
        cat_pn_wo_ver = strip_ver(cat_pn)
        if cat_pn in pkgs: continue # already exists

        pkgs.add(cat_pn) # add self
        rdepend_file = os.path.join(gentoo_dir, "var/db/pkg", cat_pn, "RDEPEND")
        if os.path.isfile(rdepend_file):
            with open(rdepend_file) as f:
                line = f.read().strip()
                if len(line) > 0:
                    rdepend_pkgnames = parse_rdepend_line(line)
                    if len(rdepend_pkgnames) > 0: scan_pkg_dep(gentoo_dir, pkg_map, rdepend_pkgnames, pkgs)

    return pkgs

def is_path_excluded(path):
    for expr in ["/run/","/var/run/","/usr/share/man/","/usr/share/doc/","/usr/share/gtk-doc/","/usr/share/info/",
        "/usr/include/","/var/cache/",re.compile(r'^/usr/lib/python[0-9\.]+?/test/'),re.compile(r'\.a$'),
        re.compile(r"\/gschemas.compiled$"), re.compile(r"\/giomodule.cache$")]:
        if isinstance(expr, re.Pattern):
            if re.search(expr, path): return True
        elif isinstance(expr, str):
            if path.startswith(expr): return True
        else:
            raise Exception("Unknown type")
    return False

def process_pkgs(gentoo_dir, packages_dir, pkgs):
    files = []
    for pkg in pkgs:
        contents_file = os.path.join(gentoo_dir, "var/db/pkg" , pkg, "CONTENTS")
        overridden_contents_file = os.path.join(packages_dir, strip_ver(pkg), "CONTENTS")
        if os.path.isfile(os.path.join(overridden_contents_file)):
            contents_file = overridden_contents_file
        if not os.path.isfile(contents_file): continue
        #else
        with open(contents_file) as f:
            while line := f.readline():
                line = re.sub(r'#.*$', "", line).strip()
                if line == "": continue
                file_to_append = None
                if line.startswith("obj "): 
                    file_to_append = re.sub(r' [0-9a-f]+ [0-9]+$', "", line[4:])
                elif line.startswith("sym "):
                    file_to_append = re.sub(r' -> .+$', "", line[4:])
                if file_to_append is not None and not is_path_excluded(file_to_append): files.append(file_to_append)
    return files

def copy(gentoo_dir, upper_dir, files):
    if not gentoo_dir.endswith('/'): gentoo_dir += '/'
    # files / dirs to shallow copy
    rsync = subprocess.Popen(sudo(["rsync", "-lptgoD", "--keep-dirlinks", "--files-from=-", gentoo_dir, upper_dir]), stdin=subprocess.PIPE)
    for f in files:
        if f.endswith("/."): continue
        f_wo_leading_slash = re.sub(r'^/', "", f)
        rsync.stdin.write(encode_utf8(f_wo_leading_slash + '\n'))
        src_path = os.path.join(gentoo_dir, f_wo_leading_slash)
        if os.path.islink(src_path):
            link = os.readlink(src_path)
            target = link[1:] if link[0] == '/' else os.path.join(os.path.dirname(f_wo_leading_slash), link)
            rsync.stdin.write(encode_utf8(target + '\n'))
    rsync.stdin.close()
    if rsync.wait() != 0: raise BaseException("rsync returned error code.")

    # dirs to deep copy
    rsync = subprocess.Popen(sudo(["rsync", "-a", "--keep-dirlinks", "--files-from=-", gentoo_dir, upper_dir]), stdin=subprocess.PIPE)
    for f in files:
        if not f.endswith("/."): continue
        f_wo_leading_slash = re.sub(r'^/', "", f)
        rsync.stdin.write(encode_utf8(f_wo_leading_slash + '\n'))
        src_path = os.path.join(gentoo_dir, f_wo_leading_slash)
    rsync.stdin.close()
    if rsync.wait() != 0: raise BaseException("rsync returned error code.")

def copyup_gcc_libs(gentoo_dir, upper_dir):
    subprocess.check_call(sudo(["systemd-nspawn", "-q", "-M", CONTAINER_NAME, "-D", gentoo_dir, "--overlay=+/:%s:/" % os.path.abspath(upper_dir), "sh", "-c", "touch -h `gcc --print-file-name=`/*.so.* && ldconfig" ]))

def remove_root_password(root_dir):
    subprocess.check_call(sudo(["sed", "-i", r"s/^root:\*:/root::/", os.path.join(root_dir, "etc/shadow") ]))

def make_ld_so_conf_latest(root_dir):
    subprocess.check_call(sudo(["touch", os.path.join(root_dir, "etc/ld.so.conf") ]))

def create_default_iptables_rules(root_dir):
    subprocess.check_call(sudo(["touch", os.path.join(root_dir, "var/lib/iptables/rules-save"), os.path.join(root_dir, "var/lib/ip6tables/rules-save")]))

def enable_services(root_dir, services):
    if not isinstance(services, list): services = [services]
    subprocess.check_call(sudo(["systemd-nspawn", "-q", "-M", CONTAINER_NAME, "-D", root_dir, "systemctl", "enable"] + services))

def pack(upper_dir, outfile):
    subprocess.check_call(sudo(["mksquashfs", upper_dir, outfile, "-noappend", "-comp", "xz", "-no-exports", "-b", "1M", "-Xbcj", "x86"]))
    subprocess.check_call(sudo(["chown", "%d:%d" % (os.getuid(), os.getgid()), outfile]))

def clean(workdir, arch, profile=None):
    portage = os.path.join(workdir, "portage.tar.xz")
    archdir = os.path.join(workdir, arch)
    stage3 = os.path.join(archdir, "stage3.tar.xz")
    profiles = os.path.join(archdir, "profiles")
    artifacts = os.path.join(archdir, "artifacts")
    subprocess.check_call(sudo(["rm", "-rf", portage, stage3, profiles, artifacts]))

if __name__ == "__main__":
    arch = os.uname().machine
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE_URL, help="Base URL contains dirs 'releases' 'snapshots'")
    parser.add_argument("--workdir", default="./work", help="Working directory to use")
    parser.add_argument("-o", "--outfile", default=None, help="Output file")
    parser.add_argument("--sync", action="store_true", default=False, help="Run emerge --sync before build gentoo")
    parser.add_argument("--bash", action="store_true", default=False, help="Enter bash before anything")
    parser.add_argument("--qemu", action="store_true", default=False, help="Run generated rootfs using qemu")
    parser.add_argument("--drm", action="store_true", default=False, help="Enable DRM(virgl) when running qemu")
    parser.add_argument("--profile", default=None, help="Override profile")
    parser.add_argument("artifact", default=[], nargs='*', help="Artifacts to build")
    args = parser.parse_args()

    artifacts = []
    if len(args.artifact) == 0 and os.path.isdir("./artifacts"):
        for i in os.listdir("./artifacts"):
            if os.path.isdir(os.path.join("./artifacts", i)): artifacts.append(i)
    else:
        artifacts += args.artifact
    
    if len(artifacts) == 0: artifacts.append("default")

    for artifact in artifacts:
        if artifact != "default" and not os.path.isdir(os.path.join("./artifacts", artifact)):
            raise BaseException("No such artifact: %s" % artifact)
        print("Processing artifact %s..." % artifact)
        if args.artifact == "clean":
            clean(args.workdir, arch, args.profile)
        else:
            outfile = main(args.base, args.workdir, arch, args.sync, args.bash, artifact, args.outfile, args.profile)
            if outfile is not None and args.qemu:
                qemu.run(outfile, os.path.join(args.workdir, "qemu.img"), args.drm)
        print("Done.")
