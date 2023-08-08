import os,re

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
            pkgs.add(pkgname)
            scan_pkg_dep(gentoo_dir, pkg_map, get_package_set(gentoo_dir, pkgname[1:]), pkgs)
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

def is_path_excluded(path, devel = False):
    exclude_patterns = ["/run/","/var/run/","/var/lock/","/var/cache/",
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
