#include <unistd.h>
#include <wait.h>
#include <sys/mount.h>

#include <libmount/libmount.h>

#include <iostream>
#include <fstream>
#include <filesystem>
#include <optional>
#include <functional>
#include <ext/stdio_filebuf.h> // for __gnu_cxx::stdio_filebuf

#include <getopt.h>

static const std::filesystem::path boot_partition("/run/initramfs/boot");
static const std::filesystem::path installed_system_image(boot_partition / "system.img");

bool is_dir(const std::filesystem::path& path)
{
    return std::filesystem::exists(path) && std::filesystem::is_directory(path);
}

bool is_file(const std::filesystem::path& path)
{
    return std::filesystem::exists(path) && std::filesystem::is_regular_file(path);
}

int help(const std::string& progname)
{
    std::cout << progname << std::endl;
    return 1;
}

bool check_system_image(const std::filesystem::path& system_image)
{
    char tempdir_rp[] = "/tmp/genpack-install-XXXXXX";
    auto tempdir = std::shared_ptr<char>(mkdtemp(tempdir_rp), [](char* p) { 
        std::filesystem::remove_all(p);
    });
    if (!tempdir) throw std::runtime_error("Failed to create temporary directory.");
    std::filesystem::path tempdir_path(tempdir.get());

    std::shared_ptr<libmnt_context> ctx(mnt_new_context(), mnt_free_context);
    //mnt_context_set_fstype_pattern(ctx.get(), fstype.c_str());
    mnt_context_set_source(ctx.get(), system_image.c_str());
    mnt_context_set_target(ctx.get(), tempdir_path.c_str());
    mnt_context_set_mflags(ctx.get(), MS_RDONLY);
    mnt_context_set_options(ctx.get(), "loop");

    if (mnt_context_mount(ctx.get()) != 0) return false;
    //else
    if (mnt_context_get_status(ctx.get()) != 1) return false;

    bool ok = false;
    try {
        const auto genpack_dir = tempdir_path / ".genpack";
        if (std::filesystem::is_directory(genpack_dir)) {
            auto print_file = [&genpack_dir](const std::string& filename) {
                std::ifstream i(genpack_dir / filename);
                if (!i) return;
                //else
                std::string content;
                i >> content;
                std::cout << filename << ": " << content << std::endl;
            };
            print_file("profile");
            print_file("artifact");
        }
        ok = true;
    }
    catch (const std::runtime_error& ex) {
        //
    }
    umount(tempdir_path.c_str());

    return ok;
}

bool is_image_file_loopbacked(const std::filesystem::path& system_image)
{
    int fd[2];
    if (pipe(fd) < 0) throw std::runtime_error("pipe() failed.");

    pid_t pid = fork();
    if (pid < 0) throw std::runtime_error("fork() failed.");

    int rst;
    bool is_loopbacked = false;
    if (pid == 0) { //child
        close(fd[0]);
        dup2(fd[1], STDOUT_FILENO);
        if (execlp("losetup", "losetup", "-j", system_image.c_str(), NULL) < 0) _exit(-1);
    } else { // parent
      close(fd[1]);
      {
        __gnu_cxx::stdio_filebuf<char> filebuf(fd[0], std::ios::in);
        std::istream f(&filebuf);
        std::string line;
        while (std::getline(f, line)) {
            is_loopbacked = true;
        }
      }
      close(fd[0]);
    }

    waitpid(pid, &rst, 0);

    if (!WIFEXITED(rst) || WEXITSTATUS(rst) != 0) return false;

    return is_loopbacked;
}

int fork(std::function<int()> func)
{
    pid_t pid = fork();
    if (pid < 0) throw std::runtime_error("fork() failed.");
    int rst;
    if (pid == 0) { //child
        _exit(func());
    }
    //else
    waitpid(pid, &rst, 0);
    return WIFEXITED(rst)? WEXITSTATUS(rst) : -1;
}

int install_to_disk(const std::filesystem::path& disk, const std::optional<std::filesystem::path>& _system_image, bool data_partition = true)
{
    auto system_image = _system_image? _system_image.value() : installed_system_image;
    if (!_system_image) {
        std::cerr << "System file image not specified. assuming " << system_image << "." << std::endl;
    }
    std::cout << "disk=" << disk << std::endl;
    std::cout << "file=" << system_image << std::endl;
    std::cout << "data_partition=" << data_partition << std::endl;

    fork([]{
        return execlp("echo", "echo", "1", "2");
    });
    return 0;
}

int install_self(const std::filesystem::path& system_image)
{
    static const std::filesystem::path current_system_image(boot_partition / "system.cur");
    static const std::filesystem::path old_system_image(boot_partition / "system.old");
    static const std::filesystem::path new_system_image(boot_partition / "system.new");

    if (!is_dir(boot_partition)) {
        throw std::runtime_error(std::string("Boot partition is not mounted on ") + boot_partition.string());
    }
    if (!check_system_image(system_image)) {
        throw std::runtime_error(std::string("Specified image file ") + system_image.string() + " is corrupt.");
    }
    if (is_file(old_system_image)) {
        std::filesystem::remove(old_system_image);
        std::cout << "Old system image removed to preserve disk space." << std::endl;
    }
    std::cout << "Copying new system image..." << std::flush;
    try {
        std::filesystem::copy_file(system_image, new_system_image);
        if (is_image_file_loopbacked(installed_system_image)) {
            std::filesystem::rename(installed_system_image, current_system_image);
            std::cout << "Original system image preserved." << std::endl;
        }
        std::filesystem::rename(new_system_image, installed_system_image);
    }
    catch (const std::filesystem::filesystem_error& e) {
        if (!std::filesystem::exists(installed_system_image)) {
            if (is_file(current_system_image)) {
                std::filesystem::rename(current_system_image, installed_system_image);
                std::cout << "Original system image restored." << std::endl;
            }
        }
        if (is_file(new_system_image)) std::filesystem::remove(new_system_image);
        throw e;
    }

    sync();

    std::cout << "Done.  Reboot system to take effects." << std::endl;

    return 0;
}

int main(int argc, char* argv[])
{
    int data_patririon = 1;
    const struct option longopts[] = {
        //{   *name,           has_arg, *flag, val },
        {  "help", no_argument, 0, 'h'},
        {  "disk", required_argument,     0, 'd' },
        { "no-data-partition",       no_argument, &data_patririon,  0  },
        {         0,                 0,     0,  0  }, // termination
    };
    int c;
    const char* optstring = "d:h";
    int longindex = 0;
    std::optional<std::filesystem::path> disk, system_image;

    while ((c = getopt_long(argc, argv, optstring, longopts, &longindex)) != -1) {
        if (c == 'h') {
            return help(argv[0]);
        } else if (c == 'd') {
            disk = optarg;
        }
    }

    if (argc > optind + 1) {
        std::cerr << "Too many system image files specified." << std::endl;
        return 1;
    }

    if (argc > optind) {
        system_image = argv[optind];
    } else if (!disk) {
        help(argv[0]);
        return 1;
    }

    try {
        return disk? install_to_disk(disk.value(), system_image, data_patririon == 1) : install_self(system_image.value());
    }
    catch (const std::exception& ex) {
        std::cerr << ex.what() << std::endl;
    }

    return 1;
}

// g++ -std=c++2a -o genpack-install genpack-install.cpp -lmount
