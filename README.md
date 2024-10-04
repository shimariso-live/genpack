# genpack
Generates squashfs-overlayfs root filesystem from Gentoo Linux

# Prerequisites

- Python 3
- sudo(if you are not root)
- git
- systemd-nspawn (in debian, it is in systemd-container package)
- squashfs-tools

# Installation

```
make
sudo make install
```

Then /usr/local/bin/genpack will be installed
