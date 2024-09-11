import os

_arch = os.uname().machine

def get():
    return _arch

def set(arch):
    global _arch
    _arch = arch