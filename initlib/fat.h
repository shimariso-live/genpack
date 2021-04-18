#include <filesystem>
bool fat_mount(int fd);
bool fat_umount();
uint64_t fat_get_free_space();
bool fat_file_exists(const std::string& name);

enum class FatFileCreateResult { OK, EXISTS, NOSPACE };

FatFileCreateResult fat_file_create(const std::string& name, uint32_t size);
