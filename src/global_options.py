import logging

_debug = False
_base = None
_workdir = None
_cpus = None
_env = {}

def read_global_options(args):
    global _debug, _base, _workdir, _cpus
    _debug = args.debug
    _base = args.base
    _workdir = args.workdir
    _cpus = args.cpus
    if args.env is not None:
        for e in args.env.split(","):
            name, value = e.split("=")
            _env[name] = value
            logging.info("Environment variable %s set to %s" % (name, value))

def cpus():
    return _cpus

def debug():
    return _debug

def base():
    return _base

def workdir():
    return _workdir

def env(name, default_value=None):
    return _env.get(name, default_value)

def env_iterate():
    for name, value in _env.items():
        yield name, value

def env_as_systemd_nspawn_args():
    args = []
    for name, value in _env.items():
        args.append("--setenv=%s=%s" % (name, value))
    return args