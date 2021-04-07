all: genpack

genpack.zip: __main__.py initlib/__init__.py initlib/initlib.cpp initlib/initlib.h util/__init__.py util/install-system-image util/expand-rw-layer
	python -m py_compile __main__.py
	rm -f $@
	zip $@ $^

genpack: genpack.zip
	echo '#!/usr/bin/env python' | cat - $^ > $@
	chmod +x $@

install: all
	cp -a genpack /usr/local/bin/

clean:
	rm -rf genpack.zip genpack __pycache__ *.squashfs

