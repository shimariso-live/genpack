import os,re,subprocess
from sudo import sudo

def get_last_modified():
    newest_mtime = 0
    for root, dirs, files in os.walk(os.path.join(".", "packages")):
        for f in files:
            mtime = os.stat(os.path.join(root, f)).st_mtime
            if mtime > newest_mtime: newest_mtime = mtime
    return newest_mtime

def get_dir(package, must_exist = False):
    package_dir = os.path.join(".", "packages", package)
    if not os.path.isdir(package_dir) and must_exist:
        raise Exception("No such package: %s" % package)
    #else
    return package_dir

def strip_ver(pkgname):
    pkgname = re.sub(r'-r[0-9]+?$', "", pkgname) # remove rev part
    last_dash = pkgname.rfind('-')
    if last_dash < 0: return pkgname
    next_to_dash = pkgname[last_dash + 1]
    return pkgname[:last_dash] if pkgname.find('/') < last_dash and (next_to_dash >= '0' and next_to_dash <= '9') else pkgname

def collect_packages(root_dir):
    pkg_map = {}
    db_dir = os.path.join(root_dir, "var/db/pkg")
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

def parse_rdepend_line(line, make_optional=False) -> set:
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

def scan_pkg_dep(gentoo_dir, pkg_map, pkgnames, masked_packages, pkgs = None, needed_by = None):
    if pkgs is None: pkgs = dict()
    for pkgname in pkgnames:
        if pkgname[0] == '@':
            pkgs[pkgname] = {"NEEDED_BY": set() if needed_by is None else {needed_by}}
            scan_pkg_dep(gentoo_dir, pkg_map, get_package_set(gentoo_dir, pkgname[1:]), masked_packages, pkgs, pkgname)
            continue
        optional = False
        if pkgname[0] == '?': 
            optional = True
            pkgname = pkgname[1:]
        if pkgname not in pkg_map:
            if optional: continue
            else: raise BaseException("Package %s not found" % pkgname)
        #else
        for cat_pn in pkg_map[pkgname]:
            # check INHERITED and skip if it contains "kernel-install" as it's considered as a kernel
            inherited_file = os.path.join(gentoo_dir, "var/db/pkg", cat_pn, "INHERITED")
            if os.path.isfile(inherited_file):
                with open(inherited_file) as f:
                    if re.search(rf"(?<!\w)(genpack-ignore|kernel-install)(?!\w)", f.read()) is not None:
                        #print("IGNORING: %s" % cat_pn)
                        continue

            if cat_pn in pkgs: # already exists
                if needed_by is not None: pkgs[cat_pn]["NEEDED_BY"].add(needed_by)
                continue

            pkgs[cat_pn] = {"NEEDED_BY": set() if needed_by is None else {needed_by}} # add self

            pkg_property_files = ["DESCRIPTION", "USE", "HOMEPAGE", "LICENSE"]
            for prop in pkg_property_files:
                prop_file = os.path.join(gentoo_dir, "var/db/pkg", cat_pn, prop)
                if os.path.isfile(prop_file):
                    with open(prop_file) as f:
                        line = f.read().strip()
                        if len(line) > 0:
                            pkgs[cat_pn][prop] = line.replace("\n", " ")            

            for depend_type in ["RDEPEND", "PDEPEND"]:
                depend_file = os.path.join(gentoo_dir, "var/db/pkg", cat_pn, depend_type)
                if os.path.isfile(depend_file):
                    with open(depend_file) as f:
                        line = f.read().strip()
                        if len(line) > 0:
                            rpdepend_pkgnames = parse_rdepend_line(line)
                            if depend_type == "PDEPEND" and masked_packages is not None:
                                rpdepend_pkgnames.difference_update(set(masked_packages))
                            if len(rpdepend_pkgnames) > 0: 
                                scan_pkg_dep(gentoo_dir, pkg_map, rpdepend_pkgnames, masked_packages, pkgs, pkgname)

    return pkgs

def is_path_excluded(path, devel = False):
    exclude_patterns = ["/run/","/var/run/","/var/lock/","/var/cache/","/usr/lib/genpack/",
        re.compile(r"\/gschemas.compiled$"), re.compile(r"\/giomodule.cache$")]
    if not devel: exclude_patterns += ["/usr/share/man/","/usr/share/doc/","/usr/share/gtk-doc/","/usr/share/info/",
        "/usr/include/",re.compile(r'^/usr/lib/python[0-9\.]+?/test/'),re.compile(r'\.a$')]
    for expr in exclude_patterns:
        if isinstance(expr, re.Pattern):
            if re.search(expr, path): return True
        elif isinstance(expr, str):
            if path.startswith(expr): return True
        else:
            raise Exception("Unknown type")
    return False

def get_all_files_of_all_packages(root_dir, pkgs, devel = False):
    files = []

    if os.access(os.path.join(root_dir, "usr/bin/genpack-get-all-package-files"), os.X_OK):
        cmdline = sudo(["chroot", root_dir, "/usr/bin/genpack-get-all-package-files"] + pkgs)
        # run cmdline as subprocess and get the output line by line. 
        process = subprocess.Popen(cmdline, stdout=subprocess.PIPE)
        for line in process.stdout:
            file_to_append = line.decode().strip()
            if not is_path_excluded(file_to_append, devel): files.append(file_to_append)
        process.wait()

        return files

    #else
    for pkg in pkgs:
        if pkg[0] == '@': continue
        contents_file = os.path.join(root_dir, "var/db/pkg" , pkg, "CONTENTS")
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
                if file_to_append is not None and not is_path_excluded(file_to_append, devel): files.append(file_to_append)
    return files

_v = r"(\d+)((\.\d+)*)([a-z]?)((_(pre|p|beta|alpha|rc)\d*)*)"
_rev = r"\d+"
_pv_re = re.compile(r"^" 
    "(?P<pn>"
    + r"[\w+][\w+-]*?"
    + "(?P<pn_inval>-"
    + _v + "(-r(" + _rev + "))?"
    + ")?)"
    + "-(?P<ver>"
    + _v
    + ")(-r(?P<rev>"
    + _rev
    + "))?"
    + r"$", re.VERBOSE | re.UNICODE)

def _pkgsplit(mypkg):
    """
    @param mypkg: pv
    @return:
    1. None if input is invalid.
    2. (pn, ver, rev) if input is pv
    """
    m = _pv_re.match(mypkg)
    if m is None or m.group("pn_inval") is not None: return None
    #else
    rev = m.group("rev")

    return (m.group("pn"), m.group("ver"), "r" + (0 if rev is None else rev))