#!/usr/bin/python

import os,argparse,subprocess,glob

KERNCACHE="/var/cache/genkernel/kerncache.tar.gz"
GENERATED_KERNEL_CONFIG="/etc/kernels/kernel-config"

def update_kernel_config(config):
    with open(config, "a") as f:
        f.truncate(0)
        with open(GENERATED_KERNEL_CONFIG) as f2:
            for line in f2:
                if line[0] != '#': f.write(line)

def main(kernelpkg,config,nocache=False,menuconfig=False):
    # emerge kernel and requirements
    subprocess.check_call(["emerge", "-u", "-bk", "--binpkg-respect-use=y", "genkernel", "eclean-kernel", "linux-sources", kernelpkg], 
        env={"PATH":os.environ["PATH"],"USE":"symlink","ACCEPT_LICENSE":"linux-fw-redistributable no-source-code"})

    genkernel_cmdline = ["genkernel", "--symlink", "--no-mountboot", "--no-bootloader", "--kernel-config=%s" % config, 
        "--kernel-config-filename=%s" % os.path.basename(GENERATED_KERNEL_CONFIG), 
        "--kernel-localversion=UNSET", "--no-keymap", "--kerncache=%s" % KERNCACHE]
    
    if menuconfig: genkernel_cmdline.append("--menuconfig")
    if (nocache or menuconfig) and os.path.exists(KERNCACHE): os.unlink(KERNCACHE)
    if os.path.exists(GENERATED_KERNEL_CONFIG): os.unlink(GENERATED_KERNEL_CONFIG)

    genkernel_cmdline.append("kernel")
    subprocess.check_call(genkernel_cmdline)

    update_kernel_config(config)
    
    # cleanup
    for old in glob.glob("/boot/*.old"):
        os.unlink(old)
    subprocess.check_call(["eclean-kernel", "-n", "1"])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/kernel-config", help="Specify kernel config file")
    parser.add_argument("--nocache", action="store_true", default=False, help="Invalidate kerncache")
    parser.add_argument("--menuconfig", action="store_true", default=False, help="Run menuconfig(implies --nocache)")
    parser.add_argument("kernelpkg", default="gentoo-sources", nargs='?', help="Kernel package ebuild name")
    args = parser.parse_args()
    main(args.kernelpkg, args.config, args.nocache, args.menuconfig)
    print("Done.")
