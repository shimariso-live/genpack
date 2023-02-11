#!/usr/bin/python
import sys,os,subprocess,itertools

files = set()

def iself(file):
    with open(file, "rb") as f:
        header = f.read(4)
        return header[0] == 0x7f and header[1] == 0x45 and header[2] == 0x4c and header[3] == 0x46

def do_elf(file):
    if file.endswith(".ko"): return # kernel modules are not worth to parse
    result = subprocess.run(["lddtree", "-l", file], stdout=subprocess.PIPE)
    if result.returncode == 0:
        for elf in result.stdout.decode("utf-8").split('\n'):
            do(elf)
    else:
        print("%s couldn't be parsed as ELF" % file, file=sys.stderr)

def isscript(file):
    with open(file, "rb") as f:
        header = f.read(3)
        return header[0] == 0x23 and header[1] == 0x21 and header[2] == 0x2f

def do_script(file):
    with open(file, "r") as f:
        line = f.readline()
    do(line[2:].strip().split(' ', 1)[0])

def do_dir(directory):
    for file in os.listdir(directory):
        do(os.path.join(directory, file))

def do(file):
    if file is None or file == "" or not os.path.exists(file) or file in files: return
    files.add(file)
    if os.path.islink(file): do(os.path.realpath(file))
    elif os.path.isfile(file):
        if iself(file): do_elf(file)
        elif isscript(file): do_script(file)
    elif os.path.isdir(file): do_dir(file)

def chunks(iterable, size):
    it = iter(iterable)
    item = list(itertools.islice(it, size))
    while item:
        yield item
        item = list(itertools.islice(it, size))

def main(argv):
    for file in argv:
        if not os.path.exists(file):
            raise Exception("%s does not exist" % file)
        do(file)
    for chunk in chunks(files, 10):
        subprocess.run(["touch", "-h"] + chunk)

if __name__ == "__main__":
    main(sys.argv[1:])
