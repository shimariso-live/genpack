env = {}

def set(name, value):
    env[name] = value

def unset(name):
    if name in env:
        del env[name]

def get(name):
    return env.get(name, None)

def iterate():
    for name, value in env.items():
        yield name, value

def get_as_systemd_nspawn_args():
    args = []
    for name, value in env.items():
        args.append("--setenv=%s=%s" % (name, value))
    return args

if __name__ == "__main__":
    set("FOO", "bar")
    set("BAZ", "qux")
    print(get("FOO"))
    print(get("BAZ"))
    print(get("QUUX"))
    for name, value in iterate():
        print(name, value)
    unset("FOO")
    print(get("FOO"))
    for name, value in iterate():
        print(name, value)
    print(get_as_systemd_nspawn_args())
    set("FOO", "bar")
    set("BAZ", "qux")
    print(get_as_systemd_nspawn_args())
    unset("FOO")
    print(get_as_systemd_nspawn_args())
    set("FOO", "bar")
    set("BAZ", "qux")
    print(get_as_systemd_nspawn_args())
    unset("BAZ")
    print(get_as_systemd_nspawn_args())
    set("FOO", "bar")
    set("BAZ", "qux")
    print(get_as_systemd_nspawn_args())
    set("BAZ", "quux")
    print(get_as_systemd_nspawn_args())
    unset("FOO")
    print(get_as_systemd_nspawn_args())
    unset("BAZ")
    print(get_as_systemd_nspawn_args())
    set("FOO", "bar")
    set("BAZ", "qux")
    print(get_as_systemd_nspawn_args())
    set("BAZ", "quux")
    print(get_as_systemd_nspawn_args())
    unset("FOO")
    print(get_as_systemd_nspawn_args())
    unset("BAZ")
    print(get_as_systemd_nspawn_args())
    set("FOO", "bar")
    set("BAZ", "qux")
    print(get_as_systemd_nspawn_args())
    set("BAZ", "quux")
    print(get_as_systemd_nspawn_args())
    unset("FOO")
    print(get_as_systemd_nspawn_args())
    unset("BAZ")
    print(get_as_systemd_nspawn_args())
    set("FOO", "bar")
    set("BAZ", "qux")
    print(get_as_systemd_nspawn_args())
    set("BAZ", "quux")
    print(get_as_systemd_nspawn_args())
    unset("FOO")
    print(get_as_systemd_nspawn_args())
    unset("BAZ")
