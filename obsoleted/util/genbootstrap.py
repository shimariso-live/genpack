#!/usr/bin/python
import os,re,argparse,subprocess
import urllib.request

BASE_URL="http://ftp.iij.ad.jp/pub/linux/gentoo/"

def decode_utf8(bin):
    return bin.decode("utf-8")

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

def curl_tar(url, dir, strip_components = 0):
    curl = subprocess.Popen(["curl", "-s", url], stdout=subprocess.PIPE)
    additional_opts = []
    if strip_components > 0: additional_opts += ["--strip-components=%d" % strip_components]
    tar = subprocess.Popen(["tar", "Jxvpf", "-"] + additional_opts + ["-C", dir], stdin=curl.stdout)
    tar.communicate()

def main(base, target_dir):
    if not os.path.isdir(target_dir): raise Exception("%s is not a directory." % target_dir)
    stage3_tarball_url = get_latest_stage3_tarball_url(base)
    curl_tar(stage3_tarball_url, target_dir)

    repos_dir = os.path.join(target_dir, "var/db/repos/gentoo")
    os.makedirs(repos_dir, exist_ok=True)
    portage_tarball_url = base + "snapshots/portage-latest.tar.xz"
    curl_tar(portage_tarball_url, repos_dir, 1)

    subprocess.check_call(["sed", "-i", "s/^root:\*:/root::/", os.path.join(target_dir, "etc/shadow")])
    with open(os.path.join(target_dir, "etc/systemd/network/50-eth0.network"), "w") as f:
        f.write("[Match]\nName=eth0 host0\n[Network]\nDHCP=yes\nMulticastDNS=yes\nLLMNR=yes\n")
    
    subprocess.check_call(["chroot", target_dir, "systemctl", "enable", "systemd-resolved"])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE_URL, help="Base URL contains dirs 'releases' 'snapshots'")
    parser.add_argument("target_dir")
    args = parser.parse_args()
    main(args.base, args.target_dir)
    print("Done.")
