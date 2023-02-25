#include <unistd.h>
#include <memory.h>
#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/reboot.h>
#include <sys/mount.h>
#include <sys/sendfile.h>
#include <sys/sysmacros.h>
#include <sys/wait.h>
#include <linux/loop.h>

#include <iostream>
#include <fstream>
#include <filesystem>
#include <optional>
#include <memory>
#include <vector>

#include <libmount/libmount.h>
#include <blkid/blkid.h>

static const std::string boottime_txt = "boottime.txt";

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

static int umount(const std::filesystem::path& mountpoint)
{
    return ::umount(mountpoint.c_str());
}

static std::optional<std::filesystem::path> get_source_device_from_mountpoint(const std::filesystem::path& path)
{
  if (!std::filesystem::exists(path) || !std::filesystem::is_directory(path)) return std::nullopt;
  // else
  std::shared_ptr<libmnt_table> tb(mnt_new_table_from_file("/proc/self/mountinfo"),mnt_unref_table);
  std::shared_ptr<libmnt_cache> cache(mnt_new_cache(), mnt_unref_cache);
  mnt_table_set_cache(tb.get(), cache.get());

  int rst = -1;
  libmnt_fs* fs = mnt_table_find_target(tb.get(), path.c_str(), MNT_ITER_BACKWARD);
  return fs? std::optional(mnt_fs_get_srcpath(fs)) : std::nullopt;
}

static std::shared_ptr<blkid_struct_cache> blkid_get_cache()
{
    blkid_cache cache;
    if (blkid_get_cache(&cache, "/dev/null") < 0) throw std::runtime_error("blkid_get_cache() failed");
    return std::shared_ptr<blkid_struct_cache>(cache, blkid_put_cache);
}

static std::optional<std::filesystem::path> search_partition(const std::string& key, const std::string& value)
{
    auto cache = blkid_get_cache();
    if (blkid_probe_all(cache.get()) < 0) throw std::runtime_error("blkid_probe_all() failed");
    std::shared_ptr<blkid_struct_dev_iterate> dev_iter(blkid_dev_iterate_begin(cache.get()),blkid_dev_iterate_end);
    if (!dev_iter)  throw std::runtime_error("blkid_dev_iterate_begin() failed");

    if (blkid_dev_set_search(dev_iter.get(), key.c_str(), value.c_str()) < 0) throw std::runtime_error("blkid_dev_set_search() failed");
    blkid_dev dev = NULL;
    while (blkid_dev_next(dev_iter.get(), &dev) == 0) {
        dev = blkid_verify(cache.get(), dev);
        if (dev) return blkid_dev_devname(dev);
    }
    //else
    return std::nullopt; // not found
}

static std::optional<std::string> get_blkid_tag(const std::filesystem::path& device, const std::string& key)
{
    auto cache = blkid_get_cache();
    auto dev = blkid_get_dev(cache.get(), device.c_str(), BLKID_DEV_NORMAL);
    if (!dev) return std::nullopt;
    //else
    std::shared_ptr<blkid_struct_tag_iterate> iter(blkid_tag_iterate_begin(dev),blkid_tag_iterate_end);
    if (!iter) return std::nullopt;
    //else
    const char *type, *value;
    while (blkid_tag_next(iter.get(), &type, &value) == 0) {
        if (strcmp(type,key.c_str()) == 0) return value;
    }
    return std::nullopt;
}

static std::optional<std::string> determine_fstype(const std::filesystem::path& device)
{
    return get_blkid_tag(device, "TYPE");
}

static std::string get_filesystem_uuid(const std::filesystem::path& device)
{
    auto uuid = get_blkid_tag(device, "UUID");
    if (!uuid) throw std::runtime_error("No filesystem UUID for " + device.string() + ".");
    //else
    return *uuid;
}

template <typename T> T read(int fd)
{
    T buf;
    auto r = read(fd, &buf, sizeof(buf));
    if (r < (ssize_t)sizeof(buf)) throw std::runtime_error("Boundary exceeded(EFI bug?)");
    return buf;
}

inline uint16_t read_le16(int fd) { return le16toh(read<uint16_t>(fd)); }
inline uint32_t read_le32(int fd) { return le32toh(read<uint32_t>(fd)); }

static std::string detect_efi_boot_partition()
{
    int bc_fd = open("/sys/firmware/efi/efivars/BootCurrent-8be4df61-93ca-11d2-aa0d-00e098032b8c", O_RDONLY);
    if (bc_fd < 0) throw std::runtime_error("Cannot access EFI vars(No efivarfs mounted?)"); // no efi firmware?
    uint16_t boot_current = 0;
    try {
        read<uint32_t>(bc_fd); // skip 4 bytes
        boot_current = read_le16(bc_fd); // current boot #
    }
    catch (...) {
        close(bc_fd);
        throw;
    }
    close(bc_fd);

    char bootvarpath[80];
    if (sprintf(bootvarpath, "/sys/firmware/efi/efivars/Boot%04X-8be4df61-93ca-11d2-aa0d-00e098032b8c", boot_current) < 0) {
        throw std::runtime_error("sprintf()");
    }
    //else
    int fd = open(bootvarpath, O_RDONLY);
    if (fd < 0) throw std::runtime_error("Cannot access EFI boot option");
    //else
    std::optional<std::string> partuuid;
    try {
        read<uint32_t>(fd); // skip 4 bytes
        read_le32(fd); // some flags
        read_le16(fd); // length of path list
        while (read_le16(fd) != 0x0000) { ; } // description

        while(!partuuid) {
            uint8_t type, subtype;
            type = read<uint8_t>(fd);
            subtype = read<uint8_t>(fd);
            if (type == 0x7f && subtype == 0xff) break; // end of device path
            // else
            auto struct_len = read_le16(fd);
            if (struct_len < 4) throw std::runtime_error("Invalid structure(length must not be less than 4)");
            if (type != 0x04/*MEDIA_DEVICE_PATH*/ || subtype != 0x01/*MEDIA_HARDDRIVE_DP*/) {
                ssize_t skip_len = struct_len - 4; 
                uint8_t buf[skip_len];
                if (read(fd, buf, skip_len) != skip_len)
                    throw std::runtime_error("Boundary exceeded(EFI bug?)");
                //else
                continue;
            }
            //else
            auto partition_number = read_le32(fd);
            read<uint64_t>(fd); // partition_start
            read<uint64_t>(fd); // partition_size
            uint8_t signature[16];
            for (size_t i = 0; i < sizeof(signature); i++) {
                signature[i] = read<uint8_t>(fd);
            }
            read<uint8_t>(fd); // mbrtype
            auto signaturetype = read<uint8_t>(fd);
            if (signaturetype == 1/*mbr*/) {
                uint32_t u32lebuf =
                    ((uint32_t)signature[0]) | ((uint32_t)signature[1] << 8) 
                    | ((uint32_t)signature[2] << 16) | ((uint32_t)signature[3] << 24);
                char buf[16];
                if (sprintf(buf, "%08x-%02d", u32lebuf, (int)partition_number) < 0) {
                    throw std::runtime_error("sprintf()");
                }
                //else
                partuuid = buf;
            } else if (signaturetype == 2/*gpt*/) {
                uint32_t u32lebuf =
                    ((uint32_t)signature[0]) | ((uint32_t)signature[1] << 8) 
                    | ((uint32_t)signature[2] << 16) | ((uint32_t)signature[3] << 24);
                uint16_t u16lebuf1 =
                    ((uint16_t)signature[4]) | ((uint16_t)signature[5] << 8);
                uint16_t u16lebuf2 =
                    ((uint16_t)signature[6]) | ((uint16_t)signature[7] << 8);
                uint16_t u16bebuf1 =
                    ((uint16_t)signature[8] << 8) | ((uint16_t)signature[9]);
                uint16_t u16bebuf2 =
                    ((uint16_t)signature[10] << 8) | ((uint16_t)signature[11]);
                uint32_t u32bebuf =
                    ((uint32_t)signature[12] << 24) | ((uint32_t)signature[13] << 16) 
                    | ((uint32_t)signature[14] << 8) | ((uint32_t)signature[15]);
                char buf[40];
                if (sprintf(buf, "%08x-%04x-%04x-%04x-%04x%08x", 
                    u32lebuf, u16lebuf1, u16lebuf2, u16bebuf1, u16bebuf2, u32bebuf) < 0) {
                    throw std::runtime_error("sprintf()");
                }
                //else
                partuuid = buf;
            }
        }
    }
    catch (...) {
        close(fd);
        throw;
    }
    close(fd);
    if (!partuuid) throw std::runtime_error("Partition not found in device path");
    //else
    return *partuuid;
}

static std::filesystem::path losetup(const std::filesystem::path& file)
{
    std::filesystem::path device = "/dev/loop0";
    auto backing_fd = open(file.c_str(), O_RDONLY);
    if (backing_fd < 0) throw std::runtime_error("open(" + file.string() +  ") failed");

    struct	loop_config config;
    memset(&config, 0, sizeof(config));
    auto loop_fd = open(device.c_str(), O_RDONLY);
    if (loop_fd < 0) {
        close(backing_fd);
        throw std::runtime_error("open(" + device.string() +  ") failed");
    }
    config.fd = backing_fd;
    strcpy((char*)config.info.lo_file_name, file.c_str());
    if (ioctl(loop_fd, LOOP_CONFIGURE, &config) < 0) throw std::runtime_error("ioctl(" + device.string() +  ", LOOP_CONFIGURE) failed");
    //else
    return device;
}

static void recursive_move(int fd, const std::filesystem::path& path)
{
    std::shared_ptr<DIR> dir(fdopendir(fd), closedir);
    if (!dir) throw std::runtime_error("failed to open directory");

	int dfd = dirfd(dir.get());
	struct stat rb;
	if (fstat(dfd, &rb) < 0) throw std::runtime_error("stat failed");
    //else
    std::filesystem::create_directories(path);

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

        // move subdirectories
		bool isdir = false;
        if (S_ISDIR(sb.st_mode)) {
            FD cfd(openat(dfd, name.c_str(), O_RDONLY));
            if (cfd >= 0) recursive_move(cfd, path / name);
            isdir = true;
        } else {
            if (S_ISREG(sb.st_mode)) {
                FD src_fd(openat(dfd, name.c_str(), O_RDONLY));
                if (src_fd >= 0) {
                    FD dst_fd(open((path / name).c_str(), O_CREAT|O_WRONLY, sb.st_mode & 07777));
                    if (dst_fd) sendfile(dst_fd, src_fd, NULL, sb.st_size);
                }
            } else if (S_ISLNK(sb.st_mode)) {
                char buf[PATH_MAX];
                auto size = readlinkat(dfd, name.c_str(), buf, sizeof(buf) - 1);
                if (size > 0) {
                    buf[size] = '\0';
                    symlink(buf, (path / name).c_str());
                }
            }
        }
        
        if (unlinkat(dfd, name.c_str(), isdir ? AT_REMOVEDIR : 0))
            std::cerr << "failed to unlink " + name << std::endl;
	}
}

static std::optional<std::filesystem::path> get_block_device_by_number(unsigned int major, unsigned int minor)
{
    for (const std::filesystem::directory_entry& x : std::filesystem::directory_iterator("/sys/class/block")) {
        if (!x.is_directory()) continue;
        if (!std::filesystem::exists(x.path() / "partition")) continue;
        {
            std::ifstream dev(x.path() / "dev");
            if (!dev) continue;
            std::string s;
            dev >> s;
            if (s != std::to_string(major) + ':' + std::to_string(minor)) continue;
        }
        {
            std::ifstream ro(x.path() / "ro");
            if (!ro) continue;
            int n;
            ro >> n;
            if (n != 0) continue;
        }
        return std::filesystem::path("/dev") / x.path().filename();
    }

    return {};
}

static std::optional<std::filesystem::path> get_data_partition(const std::filesystem::path& boot_partition_dev)
{
    auto boot_partition_uuid = get_filesystem_uuid(boot_partition_dev);
    auto data_partition = search_partition("LABEL", std::string("data-") + boot_partition_uuid);
    if (!data_partition) data_partition = search_partition("LABEL", std::string("wbdata-") + boot_partition_uuid); // for compatibility
    if (!data_partition) {
        struct stat st;
    	if (stat(boot_partition_dev.c_str(), &st) == 0) {
            auto minor = minor(st.st_rdev);
            if (minor == 1) data_partition = get_block_device_by_number(major(st.st_rdev), 2);
        }
    }

    return data_partition;
}

static bool is_fat_dirty(const std::filesystem::path device)
{
    auto fd = open(device.c_str(), O_RDONLY);
    if (fd < 0) return false;
    char buf[9] = "        ";
    if (pread(fd, buf, 8, 0x52) != 8 || strcmp(buf, "FAT32   ") != 0) {
        close(fd);
        return false; // Unknown filesystem type
    }
    uint8_t flags;
    auto dirty = pread(fd, &flags, 1, 0x41) == 1 && (flags & 1);
    close(fd);
    return dirty;
}

static void init()
{
    std::filesystem::path banner("/banner.txt");
    if (std::filesystem::exists(banner) && std::filesystem::is_regular_file(banner)) {
        {
            std::ifstream f(banner);
            if (f) {
                std::stringstream buffer;
                buffer << f.rdbuf();
                std::cout << buffer.str() << std::endl;
            }
        }
        std::filesystem::remove(banner);
    }
    std::filesystem::path dev("/dev"), sys("/sys");
    std::filesystem::create_directory(dev);
    if (mount("udev", dev, {fstype:"devtmpfs", flags:MS_NOSUID, data:"mode=0755,size=10M"}) != 0) throw std::runtime_error("Failed to mount /dev");
    std::filesystem::create_directory(sys);
    if (mount("sysfs", sys, {fstype:"sysfs", flags:MS_NOEXEC|MS_NOSUID|MS_NODEV}) != 0) throw std::runtime_error("Failed to mount /sys");

    // search boot partition using boot_partition_uuid variable given by bootloader
    const char *boot_partition_uuid = getenv("boot_partition_uuid");
    std::optional<std::filesystem::path> boot_partition_dev;
    if (boot_partition_uuid && boot_partition_uuid[0]) {
        for (int i = 0; i < 5; i++) {
            boot_partition_dev = search_partition("UUID", boot_partition_uuid);
            if (boot_partition_dev) break;
            //else
            if (i == 0) std::cout << "Waiting for boot partition to be online..." << std::endl;
            sleep(1);
        }
    }
    
    // search EFI boot partition
    auto efivars = sys / "firmware/efi/efivars";
    if (!boot_partition_dev && std::filesystem::is_directory(efivars) && mount("none", efivars, {fstype:"efivarfs"}) == 0) {
        std::cout << "Detecting boot partition from EFI vars..." << std::endl;
        try {
            auto partuuid = detect_efi_boot_partition();
            for (int i = 0; i < 5; i++) {
                boot_partition_dev = search_partition("PARTUUID", partuuid);
                if (boot_partition_dev) break;
                //else
                std::cout << "Waiting for " << partuuid << " to be online..." << std::endl;
                sleep(1);
            }
            std::cout << "EFI boot partition: " << boot_partition_dev->string() << std::endl;
        }
        catch (const std::runtime_error& err) {
            std::cout << err.what() << std::endl;
        }
    }

    if (!boot_partition_dev) throw std::runtime_error("Boot partition couldn't be determined");

    std::filesystem::path boot_partition("/boot");
    std::filesystem::create_directory(boot_partition);
    auto boot_partition_fstype = determine_fstype(*boot_partition_dev);
    if (!boot_partition_fstype) throw std::runtime_error("Unable to determine filesystem type of " + boot_partition_dev->string());

    if ((*boot_partition_fstype) == "vfat" && is_fat_dirty(*boot_partition_dev)) { 
        // perform fsck before mounting if FAT is dirty
        std::cout << "Boot partition not properly unmounted last time. Fixing..." << std::endl;
        auto pid = fork();
        if (pid == 0) _exit(execl("/usr/sbin/fsck.fat", "/usr/sbin/fsck.fat", "-a", "-w", boot_partition_dev->c_str(), NULL));
        else if (pid > 0) wait(NULL);
    }

    const auto data = (*boot_partition_fstype) == "vfat"? "codepage=437,fmask=177,dmask=077" : "";
    const unsigned int flags = (*boot_partition_fstype) == "iso9660"? MS_RDONLY : MS_RELATIME;
    if (mount(*boot_partition_dev, boot_partition, {fstype:*boot_partition_fstype, flags:flags, data:data}) != 0) {
        throw std::runtime_error("Failed to mount boot partition " + boot_partition_dev->string());
    }

    if (*boot_partition_fstype == "vfat") { // considering UEFI, writable boot partition should be FAT32
        std::ofstream time_file(boot_partition / boottime_txt);
        time_file << time(NULL);

        // rename system.cur to system.old
        if (std::filesystem::exists(boot_partition / "system.cur")) {
            if (std::filesystem::exists(boot_partition / "system.old")) std::filesystem::remove(boot_partition / "system.old");
            std::filesystem::rename(boot_partition / "system.cur", boot_partition / "system.old");
        }
    }

    auto data_partition = get_data_partition(*boot_partition_dev);
    if (!data_partition) {
        std::cout << "Data partition could not be determined." << std::endl;
    }

    auto system_img = boot_partition / "system.img";
    if (!std::filesystem::is_regular_file(system_img)) throw std::runtime_error(system_img.string() + " does not exist or not a regular file");
    auto loopback = losetup(system_img);
    std::filesystem::path newroot("/root");
    std::filesystem::create_directory(newroot);
    auto loopback_fstype = determine_fstype(loopback);
    if (!loopback_fstype) throw std::runtime_error("Filesystem type of " + system_img.string() + " couldn't be determined");
    if (mount(loopback, newroot, {fstype:*loopback_fstype, flags:MS_RDONLY}) != 0) {
        throw std::runtime_error(loopback.string() + " couldn't be mounted");
    }

    auto run = newroot / "run";
    if (mount("tmpfs", run, {fstype:"tmpfs", flags:MS_NODEV|MS_NOSUID|MS_STRICTATIME, data:"mode=755"}) < 0) {
        throw std::runtime_error("Mounting tmpfs on " + run.string() + " failed");
    }
    auto run_boot = run / ".boot";
    std::filesystem::create_directory(run_boot);
    if (mount(boot_partition, run_boot, {flags:MS_MOVE}) < 0) throw std::runtime_error("Moving mountpoint for " + boot_partition.string() + " failed");

    if (mount(sys, newroot / "sys", {flags:MS_MOVE}) < 0) throw std::runtime_error("Moving mountpoint for " + sys.string() + " failed");
    std::filesystem::remove(sys);
    if (mount(dev, newroot / "dev", {flags:MS_MOVE}) < 0) throw std::runtime_error("Moving mountpoint for " + dev.string() + " failed");
    std::filesystem::remove_all(dev);

    std::filesystem::rename("/init", "/shutdown");

    int cfd = open("/", O_RDONLY);
    if (cfd < 0) throw std::runtime_error("Unable to open /");
    //else

    chdir(newroot.c_str());
    ::mount(newroot.c_str(), "/", NULL, MS_MOVE, NULL);
    chroot(".");
    chdir("/");
    recursive_move(cfd, "/run/.shutdown");
    rmdir("/run/.shutdown/root"); // newroot mountpoint is not necessary in shutdown env
    close(cfd);
    if (execl("/sbin/overlay-init", "/sbin/overlay-init", data_partition? data_partition->c_str() : NULL, NULL) != 0) {
        throw std::runtime_error("Executing /sbin/overlay-init failed");
    }
}

static bool recursive_umount(const std::filesystem::path& mountpoint)
{
    auto pid = fork();
    if (pid < 0) return false;
    //else
    if (pid == 0) {
        _exit(execl("/bin/umount", "/bin/umount", "-R", "-n", mountpoint.c_str(), NULL));
    }
    //else
    int wstatus;
    return (waitpid(pid, &wstatus, 0) == 0 && WIFEXITED(wstatus) && WEXITSTATUS(wstatus) == 0);
}

static void shutdown()
{
    std::cout << "Unmounting filesystems..." << std::endl;
    std::filesystem::create_directory("/mnt");
    mount("/oldroot/run", "/mnt", NULL, MS_MOVE, NULL);
    recursive_umount("/oldroot");
    recursive_umount("/mnt/initramfs/ro");

    std::filesystem::path boot_filesystem_mountpoint("/mnt/initramfs/boot");
    auto boot_partition_dev = get_source_device_from_mountpoint(boot_filesystem_mountpoint);
    if (!boot_partition_dev) {
        std::cerr << "Boot partition not found" << std::endl;
        return;
    }
    //else
    if (determine_fstype(*boot_partition_dev).value_or("") != "vfat") return; // only vfat needs cleanup

    std::cout << "Cleaning up boot partition..." << std::endl;
    if (mount("none", boot_filesystem_mountpoint.c_str(), NULL, MS_RELATIME|MS_REMOUNT, NULL) == 0) {
        if (std::filesystem::exists(boot_filesystem_mountpoint / boottime_txt)) {
            std::filesystem::remove(boot_filesystem_mountpoint / boottime_txt);
        }
    } else {
        std::cerr << "Boot partition cannot be remounted R/W." << std::endl;
    }

    if (umount(boot_filesystem_mountpoint.c_str()) != 0) {
        std::cerr << "Unmounting boot partition failed." << std::endl;
        return;
    }

    std::cout << "Cleaning up boot partition done." << std::endl;
}

int print_dependencies()
{
    std::cout << "/init /bin/umount /usr/sbin/fsck.fat";
    if (std::filesystem::exists("/usr/lib64/gconv/gconv-modules.cache")) {
        std::cout << " /usr/lib64/gconv/gconv-modules.cache /usr/lib64/gconv/IBM850.so";
    } else if (std::filesystem::exists("/usr/lib/gconv/gconv-modules.cache")) {
        std::cout << " /usr/lib/gconv/gconv-modules.cache /usr/lib/gconv/IBM850.so";
    }
    std::cout << std::endl;
    return 0;
}

int main(int argc, char* argv[])
{
    std::filesystem::path progname(argv[0]);

    if (progname == "/shutdown") {
        try {
            shutdown();
        }
        catch (const std::runtime_error& err) {
            std::cerr << err.what() << std::endl;
        }
        auto arg = argc > 1? std::optional(std::string(argv[1])) : std::nullopt;
        if (arg == "poweroff") {
            reboot(RB_POWER_OFF);
        } else if (arg == "reboot") {
            reboot(RB_AUTOBOOT);
        }
        //else
        reboot(RB_HALT_SYSTEM);
    } else { // /init
        if (getpid() != 1) {
            return print_dependencies();
        }
        //else
        try {
            init();
        }
        catch (const std::runtime_error& err) {
            std::cerr << err.what() << std::endl;
        }
        reboot(RB_HALT_SYSTEM);
    }
    return 0; // no reach here
}

// g++ -std=c++20 -static-libgcc -static-libstdc++ -o /init init-systemimg.cpp -lblkid -lmount
