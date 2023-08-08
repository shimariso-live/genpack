import os,uuid,subprocess
import arch
from sudo import sudo

_root = os.path.join(os.getcwd(), "work")

def set(root):
    global _root
    _root = root

def get(relpath = None, create = True):
    if relpath is not None and relpath.startswith("/"): relpath = relpath[1:]
    path = os.path.join(_root, relpath) if relpath is not None else _root
    if not os.path.exists(path) and create:
        os.makedirs(path)
        # create .gitignore right under workdir root if it does not exist
        gitignore = os.path.join(_root, ".gitignore")
        if not os.path.exists(gitignore):
            with open(gitignore, "w") as f:
                f.write("/*")
    return path

def get_arch(relpath = None, create = True):
    if relpath is not None and relpath.startswith("/"): relpath = relpath[1:]
    path = os.path.join(arch.get(), relpath) if relpath is not None else arch.get()
    return get(path, create)

def get_portage(create=True):
    return get("portage", create)

def get_profile(profile, relpath = None, create=True):
    if relpath is not None and relpath.startswith("/"): relpath = relpath[1:]
    path = os.path.join(profile, relpath) if relpath is not None else profile
    return get_arch(os.path.join("profiles", path), create)

def get_artifact(artifact, relpath = None, create=True):
    if relpath is not None and relpath.startswith("/"): relpath = relpath[1:]
    path = os.path.join(artifact, relpath) if relpath is not None else artifact
    return get_arch(os.path.join("artifacts", path), create)

def get_trash(create=True):
    return get("trash", create)

def move_to_trash(path, noexist_ok = False):
    if not os.path.exists(path):
        if noexist_ok: return
        #else
        raise Exception("No such file or directory: %s" % path)
    #else
    trash_dir = get_trash()
    os.makedirs(trash_dir, exist_ok=True)
    subprocess.check_call(sudo(["mv", path, os.path.join(trash_dir, str(uuid.uuid4()))]))

def cleanup_trash():
    trash_dir = get_trash(False)
    if not os.path.exists(trash_dir): return
    #else
    print("Cleaning up %s..." % trash_dir)
    subprocess.check_call(sudo(["rm", "-rf", trash_dir]))

def clean():
    archdir = get_arch(None, False)
    profiles = os.path.join(archdir, "profiles")
    artifacts = os.path.join(archdir, "artifacts")
    subprocess.check_call(sudo(["rm", "-rf", profiles, artifacts]))

