#!/usr/bin/python
import os,argparse,subprocess,shutil,stat,time

DATADIR="/var/lib/mysql"
SOCKET="/var/run/mysqld/mysqld.sock"
MYSQLD="/usr/sbin/mysqld"

class MySQL:
    def __init__(self, datadir, socket):
        self.datadir = datadir
        self.socket = socket
    
    def is_mysql_running(self):
        return os.path.exists(self.socket) and stat.S_ISSOCK(os.stat(self.socket).st_mode)
    
    def wait_for_mysql_to_be_up(self, maxtry=15):
        for i in range(0,maxtry):
            if self.is_mysql_running(): return
            #else
            time.sleep(1)
        # give up
        raise BaseException("MySQL didn't come up")

    def __enter__(self):
        if self.is_mysql_running(): raise BaseException("MySQL is already running")
        #else

        socket_dir = os.path.dirname(self.socket)
        if not os.path.isdir(socket_dir):
            os.makedirs(socket_dir)
            shutil.chown(socket_dir, user="mysql", group="mysql")

        self.mysql = subprocess.Popen([MYSQLD, 
            "--no-defaults", "--skip-networking", "--user=mysql", "--log_error_verbosity=1", "--basedir=/usr", 
            "--datadir=%s" % self.datadir, "--max_allowed_packet=8M", "--net_buffer_length=16K", "--skip-log-bin",
            "--log-error=/tmp/mysqld.err", "--socket=%s" % self.socket])
        print("Waiting for MySQL to start...")
        self.wait_for_mysql_to_be_up()
        print("MySQL started.")

        return self.mysql

    def __exit__(self, exception_type, exception_value, traceback):
        print("Shutting down MySQL...")
        self.mysql.terminate()
        self.mysql.wait()
        print("Shutdown completed.")

def main(datadir, socket, commands):
    if not os.path.isdir(datadir):
        print("Data directory %s does not exist. creating." % datadir)
        os.makedirs(datadir, exist_ok=True)
        shutil.chown(datadir, user="mysql", group="mysql")
        os.chmod(datadir, 0o750)

    new_data = False
    if not os.path.isdir(os.path.join(datadir, "mysql")):
        print("MySQL has not been initialized yet.  Initializing...")
        subprocess.check_call([MYSQLD, "--log-error=/tmp/mysqld.err", "--initialize-insecure", "--skip-log-bin", "--user=mysql", "--datadir=%s" % datadir])
        new_data = True

    with MySQL(datadir, socket) as mysql:
        if new_data and os.path.isfile("/usr/bin/mysql_tzinfo_to_sql") and os.path.isdir("/usr/share/zoneinfo"):
            print("Initializing timezone database...")
            subprocess.check_call("/usr/bin/mysql_tzinfo_to_sql /usr/share/zoneinfo | /usr/bin/mysql -uroot mysql", shell=True)
        for command in commands:
            subprocess.check_call(command, shell=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--datadir", default=DATADIR, help="MySQL data directory")
    parser.add_argument("--socket", default=SOCKET, help="MySQL socket")
    parser.add_argument("command", nargs='+', help="Shell commands to execute")
    args = parser.parse_args()
    main(args.datadir, args.socket, args.command)
