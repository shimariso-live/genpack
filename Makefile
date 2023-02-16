all: genpack genpack-install

genpack.zip: __main__.py qemu.py sudo.py \
		initlib/__init__.py initlib/initlib.cpp initlib/initlib.h initlib/fat.cpp initlib/fat.h \
		init/__init__.py init/init.cpp init/init.h \
		util/__init__.py util/install-system-image util/expand-rw-layer util/do-with-lvm-snapshot util/build-kernel.py \
		util/download.py util/recursive-touch.py util/overlay_init.py util/with-mysql.py util/genpack-install.cpp
	python -m py_compile __main__.py
	rm -f $@
	zip $@ $^

genpack: genpack.zip
	echo '#!/usr/bin/env python' | cat - $^ > $@
	chmod +x $@

genpack-install: util/genpack-install.cpp
	g++ -std=c++2a -o $@ $^ -lmount -lblkid

install: all
	cp -a genpack /usr/local/bin/
	cp -a genpack-install /usr/local/sbin/

clean:
	rm -rf genpack.zip genpack __pycache__ *.squashfs genpack-install

