all: libinitlib.a

.cpp.o:
	g++ -std=c++2a -c $<

libinitlib.a: initlib.o
	ar r $@ $^

clean:
	rm  -f *.o  libinitlib.a

