import os,argparse,subprocess,tempfile

def sudo(cmdline):
    if os.geteuid() == 0: return cmdline
    return ["sudo"] + cmdline

class Tee():
    def __init__(self, filename):
        self.filename = filename
    def __enter__(self):
        self.process = subprocess.Popen(sudo(["tee", self.filename]), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        return self.process.stdin
    def __exit__(self, exception_type, exception_value, traceback):
        self.process.stdin.close()
        self.process.wait()
