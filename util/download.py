#!/usr/bin/python
import os,sys,argparse,hashlib,subprocess,shutil

CACHE_DIR="/var/cache/download"

def main(cachedir, url):
    url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()
    obj_path = os.path.join(cachedir, url_hash)

    os.makedirs(cachedir, exist_ok=True)
    cmdline = ["curl", "-o", obj_path]
    if os.path.exists(obj_path): cmdline += ["-z", obj_path]
    cmdline.append(url)
    subprocess.check_call(cmdline, stdout=subprocess.DEVNULL)

    with open(obj_path, "rb") as f:
        shutil.copyfileobj(f, sys.stdout.buffer)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cachedir", default=CACHE_DIR, help="Cache directory")
    parser.add_argument("url")
    args = parser.parse_args()
    main(args.cachedir, args.url)
