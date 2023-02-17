#include <dirent.h>
#include <string.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/reboot.h>
#include <sys/mount.h>

#include <iostream>
#include <fstream>
#include <filesystem>
#include <optional>
#include <memory>
#include <vector>

#include <blkid/blkid.h>

struct MountOptions {
    const std::string fstype = "auto";
    const unsigned int flags = MS_RELATIME;
    const std::string data = "";
};

static int mount(const std::filesystem::path& source,
  const std::filesystem::path& mountpoint,
  const MountOptions& options = {})
{
  return ::mount(source.c_str(), mountpoint.c_str(), options.fstype.c_str(), options.flags, options.data.c_str());
}

static std::optional<std::string> determine_fstype(const std::filesystem::path& device)
{
    blkid_cache _cache;
    if (blkid_get_cache(&_cache, "/dev/null") < 0) throw std::runtime_error("blkid_get_cache() failed");
    std::shared_ptr<blkid_struct_cache> cache(_cache, blkid_put_cache);

    auto dev = blkid_get_dev(cache.get(), device.c_str(), BLKID_DEV_NORMAL);
    if (!dev) return std::nullopt;
    //else
    std::shared_ptr<blkid_struct_tag_iterate> iter(blkid_tag_iterate_begin(dev),blkid_tag_iterate_end);
    if (!iter) return std::nullopt;
    //else
    const char *type, *value;
    while (blkid_tag_next(iter.get(), &type, &value) == 0) {
        if (strcmp(type,"TYPE") == 0) return value;
    }
    return std::nullopt;
}

static void recursive_remove(int fd)
{
    std::shared_ptr<DIR> dir(fdopendir(fd), closedir);
    if (!dir) throw std::runtime_error("failed to open directory");

	int dfd = dirfd(dir.get());
	struct stat rb;
	if (fstat(dfd, &rb) < 0) throw std::runtime_error("stat failed");
    //else

    class FD {
        int fd = -1;
    public:
        FD(int _fd) : fd(_fd) { ; }
        ~FD() { if (fd >= 0) close(fd); }
        operator int() { return fd; }
    };

	while(true) {
		struct dirent *d;

		errno = 0;
		if (!(d = readdir(dir.get()))) {
			if (errno) throw std::runtime_error("failed to read directory");
            //else
			break;	// end of directory
		}

        std::string name = d->d_name;

		if (name == "." || name == "..") continue;
        //else
        struct stat sb;

        if (fstatat(dfd, name.c_str(), &sb, AT_SYMLINK_NOFOLLOW) < 0) {
            std::cerr << "stat of " + name + " failed" << std::endl;
            continue;
        }

        // skip if device is not the same
        if (sb.st_dev != rb.st_dev) continue;

        // delete subdirectories
		bool isdir = false;
        if (S_ISDIR(sb.st_mode)) {
            FD cfd(openat(dfd, name.c_str(), O_RDONLY));
            if (cfd >= 0) recursive_remove(cfd);
            isdir = true;
        }
        
        if (unlinkat(dfd, name.c_str(), isdir ? AT_REMOVEDIR : 0))
            std::cerr << "failed to unlink " + name << std::endl;
	}
}

static void init()
{
    std::filesystem::path dev("/dev"), sys("/sys");
    std::filesystem::create_directory(dev);
    if (mount("udev", dev, {fstype:"devtmpfs", flags:MS_NOSUID, data:"mode=0755,size=10M"}) != 0) throw std::runtime_error("Failed to mount /dev");
    std::filesystem::create_directory(sys);
    if (mount("sysfs", sys, {fstype:"sysfs", flags:MS_NOEXEC|MS_NOSUID|MS_NODEV}) != 0) throw std::runtime_error("Failed to mount /sys");

    std::filesystem::path newroot("/root");
    std::filesystem::create_directory(newroot);
    auto system_fstype = determine_fstype("/dev/vdb");
    if (!system_fstype) throw std::runtime_error("System filesystem type couldn't be determined");
    if (mount("/dev/vdb", newroot, {fstype:*system_fstype, flags:MS_RDONLY}) != 0) {
        throw std::runtime_error("System filesystem couldn't be mounted");
    }

    auto run = newroot / "run";
    if (mount("tmpfs", run, {fstype:"tmpfs", flags:MS_NODEV|MS_NOSUID|MS_STRICTATIME, data:"mode=755"}) < 0) {
        throw std::runtime_error("Mounting tmpfs on " + run.string() + " failed");
    }
    if (mount(sys, newroot / "sys", {flags:MS_MOVE}) < 0) throw std::runtime_error("Moving mountpoint for " + sys.string() + " failed");
    std::filesystem::remove(sys);
    if (mount(dev, newroot / "dev", {flags:MS_MOVE}) < 0) throw std::runtime_error("Moving mountpoint for " + dev.string() + " failed");
    std::filesystem::remove_all(dev);

    int cfd = open("/", O_RDONLY);
    if (cfd < 0) throw std::runtime_error("Unable to open /");
    //else

    chdir(newroot.c_str());
    ::mount(newroot.c_str(), "/", NULL, MS_MOVE, NULL);
    chroot(".");
    chdir("/");
    recursive_remove(cfd);
    close(cfd);
    if (execl("/sbin/overlay-init", "/sbin/overlay-init", "/dev/vdb", NULL) != 0) {
        throw std::runtime_error("Executing /sbin/overlay-init failed");
    }
}

int main(int argc, char* argv[])
{
    if (getpid() != 1) {
        std::cerr << "PID must be 1" << std::endl;
        return 1;
    }
    //else
    try {
        init();
    }
    catch (const std::runtime_error& err) {
        std::cerr << err.what() << std::endl;
    }
    reboot(RB_HALT_SYSTEM);
    return 0; // no reach here
}

// g++ -std=c++20 -static -o /init init-paravirt.cpp -lblkid
