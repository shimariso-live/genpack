#!/usr/bin/python
import sys,os,subprocess,itertools,argparse

files = set()
dirs = set()

def iself(file):
    with open(file, "rb") as f:
        header = f.read(4)
        return header[0] == 0x7f and header[1] == 0x45 and header[2] == 0x4c and header[3] == 0x46

def do_elf(file, dereference=False):
    if file.endswith(".ko"): return # kernel modules are not worth to parse
    result = subprocess.run(["lddtree", "-l", file], stdout=subprocess.PIPE)
    if result.returncode == 0:
        for elf in result.stdout.decode("utf-8").split('\n'):
            do(elf, dereference)
    else:
        print("%s couldn't be parsed as ELF" % file, file=sys.stderr)

def isscript(file):
    with open(file, "rb") as f:
        header = f.read(3)
        return header[0] == 0x23 and header[1] == 0x21 and header[2] == 0x2f

def do_script(file, dereference=False):
    with open(file, "r") as f:
        line = f.readline()
    do(line[2:].strip().split(' ', 1)[0], dereference)

def do_dir(directory, dereference=False):
    for file in os.listdir(directory):
        do(os.path.join(directory, file), dereference)

def resolve_symlink(symlink_path):
    direct_target = os.readlink(symlink_path)
    symlink_dir = os.path.dirname(symlink_path)
    direct_target_abs = os.path.normpath(os.path.join(symlink_dir, direct_target))
    return direct_target_abs

def do(file, dereference=False):
    if file is None or file == "" or not os.path.exists(file) or file in files: return
    files.add(file)
    if not dereference and os.path.islink(file): do(resolve_symlink(file), dereference)
    elif os.path.isfile(file):
        if iself(file): do_elf(file, dereference)
        elif isscript(file): do_script(file, dereference)
    elif os.path.isdir(file): do_dir(file, dereference)

def chunks(iterable, size):
    it = iter(iterable)
    item = list(itertools.islice(it, size))
    while item:
        yield item
        item = list(itertools.islice(it, size))

def print_dir(directory):
    if directory in dirs: return
    #else
    parent = os.path.dirname(directory)
    if parent is not None and parent != "" and parent != "/": print_dir(parent)
    print(directory)
    dirs.add(directory)

def main(argv, print_for_initramfs=False, dereference=False):
    for file in argv:
        if not os.path.exists(file):
            raise Exception("%s does not exist" % file)
        do(file, dereference)
    if print_for_initramfs:
        for file in files:
            print_dir(os.path.dirname(file))
            print(file)
    else:
        for chunk in chunks(files, 10):
            subprocess.run(["touch", "-h"] + chunk)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-for-initramfs", help="Print filenames instead of touching", action="store_true")
    parser.add_argument("--dereference", help="Dereference symlinks", action="store_true")
    parser.add_argument("files", nargs='*')
    args = parser.parse_args()
    main(args.files, args.print_for_initramfs, args.dereference);
