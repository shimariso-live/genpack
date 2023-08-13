#!/usr/bin/python3
# Copyright (c) 2021-2023 Walbrix Corporation
# https://github.com/wbrxcorp/genpack/blob/main/LICENSE

import os,sys,subprocess,atexit,logging
import upstream,workdir,genpack_profile,genpack_artifact,qemu
from sudo import sudo

def prepare(args):
    profiles = []
    if len(args.profile) == 0 and os.path.isdir("./profiles"):
        profiles += genpack_profile.Profile.get_all_profiles()
    else:
        for profile in args.profile:
            profiles.append(genpack_profile.Profile(profile))
    if len(profiles) == 0: profiles.append(genpack_profile.Profile("default"))

    for profile in profiles:
        print("Preparing profile %s..." % profile.name)
        genpack_profile.prepare(profile, args.sync, "force" if args.force_executing_prepare_script else True)

def bash(args):
    profile = genpack_profile.Profile(args.profile)
    genpack_profile.bash(profile)

def build(args):
    artifacts = []
    if len(args.artifact) == 0 and os.path.isdir("./artifacts"):
        artifacts += genpack_artifact.Artifact.get_all_artifacts()
    else:
        for artifact in args.artifact:
            artifacts.append(genpack_artifact.Artifact(artifact))
    
    if len(artifacts) == 0: artifact.append(genpack_artifact.Artifact("default"))

    profiles = set()

    for artifact in artifacts:
        profiles.add(artifact.get_profile())

    for profile in profiles:
        print("Preparing profile %s..." % profile.name)
        genpack_profile.prepare(profile)

    for artifact in artifacts:
        if artifact.is_up_to_date():
            print("Artifact %s is up-to-date" % artifact.name)
        else:
            print("Building artifact %s..." % artifact.name)
            genpack_artifact.build(artifact)
        if not artifact.is_outfile_up_to_date():
            print("Packing artifact %s..." % artifact.name)
            genpack_artifact.pack(artifact)

    print("Done.")
    
def run(args):
    artifact = genpack_artifact.Artifact(args.artifact)

    if not artifact.is_up_to_date():
        print("Artifact %s is not up-to-date" % artifact.name)
        sys.exit(1)

    print("Pressing ']' 3 times will exit the container and return to the host.")
    cmdline = ["systemd-nspawn", "--suppress-sync=true", "-M", "genpack-run-%d" % os.getpid(), "-q", "-D", artifact.get_workdir(), "--network-veth"]
    if args.bash: cmdline.append("/bin/bash")
    else: cmdline.append("-b")
    subprocess.call(sudo(cmdline))

def _qemu(args):
    artifact = genpack_artifact.Artifact(args.artifact)
    outfile = artifact.get_outfile()
    qemu.run(outfile, os.path.join(args.workdir, "qemu.img"), args.drm, args.data_volume, args.system_ini)

def clean(args):
    subprocess.check_call(sudo(["rm", "-rf", workdir.get(None, False)]))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--base", default=None, help="Base URL contains dirs 'releases' 'snapshots'")
    parser.add_argument("--workdir", default=None, help="Working directory to use(default:./work)")

    subparsers = parser.add_subparsers()
    # prepare subcommand
    prepare_parser = subparsers.add_parser('prepare', help='Prepare profiles')
    prepare_parser.add_argument('profile', nargs='*', default=[], help='Profiles to prepare')
    prepare_parser.add_argument('--sync', action='store_true', help='Run emerge --sync before preparation')
    prepare_parser.add_argument('--force-executing-prepare-script', action='store_true', help='Force to execute prepare script')
    prepare_parser.set_defaults(func=prepare)

    # bash subcommand
    bash_parser = subparsers.add_parser('bash', help='Run bash on a profile')
    bash_parser.add_argument('profile', nargs='?', default='default', help='Profile to run bash')
    bash_parser.set_defaults(func=bash)

    # build subcommand
    build_parser = subparsers.add_parser('build', help='Build artifacts')
    build_parser.add_argument("artifact", default=[], nargs='*', help="Artifacts to build")
    build_parser.set_defaults(func=build)

    # run subcommand
    run_parser = subparsers.add_parser('run', help='Run an artifact')
    run_parser.add_argument('--bash', action='store_true', help='Run bash instead of spawning container')
    run_parser.add_argument('artifact', nargs='?', default='default', help='Artifact to run')
    run_parser.set_defaults(func=run)

    # qemu subcommand
    qemu_parser = subparsers.add_parser('qemu', help='Run an artifact using qemu')
    qemu_parser.add_argument('artifact', nargs='?', default='default', help='Artifact to run')
    qemu_parser.add_argument('--drm', action='store_true', help='Enable DRM(virgl) when running qemu')
    qemu_parser.add_argument('--data-volume', action='store_true', help='Create data partition when running qemu')
    qemu_parser.add_argument('--system-ini', help='system.ini file when running qemu')
    qemu_parser.set_defaults(func=_qemu)

    # clean subcommand
    clean_parser = subparsers.add_parser('clean', help='Clean up artifacts')
    clean_parser.add_argument('artifact', nargs='?', default='default', help='Artifact to clean')
    clean_parser.set_defaults(func=clean)

    args = parser.parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
        logging.debug("Debug mode enabled")

    if args.base is not None: 
        upstream.set_base_url(args.base)
        print("Base URL set to %s" % args.base)
    if args.workdir is not None:
        workdir.set(args.workdir)
        print("Working directory set to %s" % args.workdir)

    import genpack_json
    genpack_json.load()

    if not hasattr(args, 'func'):
        parser.print_help()
        sys.exit(1)
    #else
    atexit.register(workdir.cleanup_trash)
    args.func(args)
