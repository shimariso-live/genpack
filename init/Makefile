all: init.bin init-paravirt.bin

init.bin: init.cpp
	g++ -std=c++2a -static-libgcc -static-libstdc++ $< -o $@ -liniparser4 -lmount -lblkid

init-paravirt.bin: init.cpp
	g++ -std=c++2a -static-libgcc -static-libstdc++ $< -o $@ -lmount -lxenstore -lblkid -DPARAVIRT
