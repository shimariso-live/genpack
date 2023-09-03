import os,re,subprocess,logging
import arch

_base_url = "http://ftp.iij.ad.jp/pub/linux/gentoo/"
_downloaded = set()

def set_base_url(base_url):
    global _base_url
    _base_url = base_url
    if not _base_url.endswith('/'): base_url += '/'

def url_readlines(url):
    import urllib.request
    with urllib.request.urlopen(url) as f:
        return f.read().decode('utf-8').splitlines()

def get_latest_stage3_tarball_url(variant = "systemd-mergedusr"):
    _arch = arch.get()
    _arch2 = arch.get()
    if _arch == "x86_64": _arch = _arch2 = "amd64"
    elif _arch == "i686": _arch = "x86"
    elif _arch == "aarch64": _arch = _arch2 = "arm64"
    for line in url_readlines(_base_url + "releases/" + _arch + "/autobuilds/latest-stage3-" + _arch2 + "-%s.txt" % (variant,)):
        line = re.sub(r'#.*$', "", line.strip())
        if line == "": continue
        #else
        splitted = line.split(" ")
        if len(splitted) < 2: continue
        #else
        return _base_url + "releases/" + _arch + "/autobuilds/" + splitted[0]
    #else
    raise Exception("No stage3 tarball (arch=%s,variant=%s) found", arch.get(), variant)

def get_latest_portage_tarball_url():
    return _base_url + "snapshots/portage-latest.tar.xz"

def get_content_length(url):
    import urllib.request
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req) as f:
            content_length = f.headers.get("Content-Length")
            if content_length is not None:
                content_length = int(content_length)
                return content_length
    except urllib.error.HTTPError as e:
        logging.warning("HTTPError: %s", e)
        return None
    #else
    logging.warning("Failed to get Content-Length for %s", url)
    return None

def download_if_necessary(url, save_as):
    if (url,save_as) in _downloaded: return False
    _downloaded.add((url,save_as))
    if os.path.exists(save_as):
        content_length = get_content_length(url)
        if content_length is None or os.path.getsize(save_as) == get_content_length(url):
            print("Skipping download of %s" % url)
            return False
    #else
    print("Downloading %s" % url)
    # download using wget
    subprocess.check_call(["wget", "-O", save_as, url])
    return True

if __name__ == "__main__":
    import user_dir
    with user_dir.stage3_tarball() as stage3_tarball:
        download_if_necessary(get_latest_stage3_tarball_url(), stage3_tarball)
    with user_dir.portage_tarball() as portage_tarball:
        download_if_necessary(get_latest_portage_tarball_url(), portage_tarball)