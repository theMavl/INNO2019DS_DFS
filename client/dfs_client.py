import grpc

import dfs_pb2_grpc
import dfs_pb2
from google.protobuf.empty_pb2 import Empty
import os
import json
import hashlib
from pprint import pprint
import random
from multiprocessing import Pool
import sys

# SERVER_ADDRESS = "3.248.214.33:23333"

CHUNK_SIZE = 1024 * 1024


def get_chunk(chunk_UUID, hosts):
    chunk_found = False

    if type(hosts) is not list:
        hosts = [hosts]

    host = None
    shuffled_indexes = list(range(len(hosts)))
    random.shuffle(shuffled_indexes)

    for i in shuffled_indexes:
        host = hosts[i]
        # print("Request {} from {}".format(chunk_UUID, host))
        channel = None
        try:
            channel = grpc.insecure_channel(host)
            stub = dfs_pb2_grpc.DFS_StorageServerStub(channel)
            response = stub.has(dfs_pb2.ChunkUUID(chunk_uuid=chunk_UUID, timeout=5))
            if response.success:
                print("Downloading {} from {}".format(chunk_UUID, host))
                channel.close()
                chunk_found = True
                break
            else:
                print("ERR", response.response)
        except Exception as e:
            print("ERR@", e)

        if channel:
            channel.close()

    if not chunk_found:
        print("Chunk is not found, aborting...")
        return False

    channel = grpc.insecure_channel(host)
    stub = dfs_pb2_grpc.DFS_StorageServerStub(channel)
    response = stub.get(dfs_pb2.ChunkUUID(chunk_uuid=chunk_UUID))

    with open(chunk_UUID, 'wb+') as f:
        for r in response:
            f.write(r.Content)

    channel.close()
    return True


class Session:
    def __init__(self, server_address):
        self.cwd = "/"
        channel = grpc.insecure_channel(server_address)
        self.stub = dfs_pb2_grpc.DFS_NamingServerStub(channel)

        self.command_handlers = {
            "init": self.init_handler,
            "ls": self.ls_handler,
            "cd": self.cd_handler,
            "info": self.info_handler,
            "which": self.which_handler,
            "which_chunk": self.which_chunk_handler,
            "mkdir": self.mkdir_handler,
            "sl": self.sl_handler,
            "touch": self.touch_handler,
            "get": self.get_handler,
            "cp": self.cp_handler,
            "mv": self.mv_handler,
            "rm": self.rm_handler,
            "write": self.write_handler,
            "free": self.free_handler,
            "UNKNOWN": self.unknown_handler
        }

    def get_handler(self, args):
        if len(args) < 2:
            print("Usage: get [REMOTE] [DESTINATION]")
            return

        remote_path = args[0]
        local_path = args[1]
        request = dfs_pb2.Path(cwd=self.cwd, filename=remote_path)
        response = self.stub.which(request)
        response_json = json.loads(response.response)
        if response_json is None:
            print("Server returned bad JSON")
            return

        print(response_json)

        checksum = response_json["hash"]
        chunks = response_json["chunks"]

        pool = Pool(processes=4)
        results = [pool.apply_async(get_chunk, args=(c, chunks[c])) for c in chunks]

        results = [p.get() for p in results]

        if False in results:
            print("An error during downloading occurred. Dismissing chunks.")
            chunk_files = chunks.keys()
            for cf in chunk_files:
                if os.path.exists("./" + cf):
                    print("Remove", cf)
                    os.remove("./" + cf)
            return
        else:
            print("All chunks downloaded successfully.")

        chunk_files = chunks.keys()

        sha1 = hashlib.sha1()

        with open(local_path, 'wb+') as outfile:
            for cf in chunk_files:
                if not os.path.exists("./" + cf):
                    print("Download error occurred!")
                    return

                with open(cf, 'rb') as infile:
                    bf = infile.read(1024)
                    while bf:
                        outfile.write(bf)
                        sha1.update(bf)
                        bf = infile.read(1024)

                os.remove("./" + cf)

        sha1 = sha1.hexdigest()
        print("{} <-> {}".format(sha1, checksum))

        if not sha1 == checksum:
            print("File is corrupted (checksum mismatch)! Dismissing downloaded file.")
            os.remove(local_path)
        else:
            print("No errors detected.")

        return

    def mkdir_handler(self, path):
        filename = "".join(path)
        if filename == "":
            print("mkdir: missing operand")
            return

        request = dfs_pb2.Path(cwd=self.cwd, filename=filename)
        response = self.stub.mkdir(request)
        if not response.success:
            print(response)

    def ls_handler(self, path):
        filename = "".join(path)
        if filename == "":
            filename = "."

        request = dfs_pb2.Path(cwd=self.cwd, filename=filename)
        response = self.stub.ls(request)
        print(response.response)

    def info_handler(self, path):
        filename = "".join(path)
        if filename == "":
            print("info: missing operand")
            return

        request = dfs_pb2.Path(cwd=self.cwd, filename=filename)
        response = self.stub.info(request)
        if response.success:
            response_di = json.loads(response.response)
            pprint(response_di)
        else:
            print(response.response)

    def touch_handler(self, path):
        filename = "".join(path)
        if filename == "":
            print("touch: missing operand")
            return

        request = dfs_pb2.Path(cwd=self.cwd, filename=filename)
        response = self.stub.touch(request)
        if not response.success:
            print(response)

    def which_chunk_handler(self, chunk):
        chunk_uuid = "".join(chunk)
        if chunk_uuid == "":
            print("which_chunk: missing operand")
            return

        request = dfs_pb2.ChunkUUID(chunk_uuid=chunk_uuid)
        response = self.stub.which_chunk(request)
        for res in response:
            print(res)

    def which_handler(self, path):
        filename = "".join(path)
        if filename == "":
            print("which: missing operand")
            return

        request = dfs_pb2.Path(cwd=self.cwd, filename=filename)
        response = self.stub.which(request)
        # print(response)
        if response.success:
            chunks_json = json.loads(response.response)
            pprint(chunks_json)

    def cd_handler(self, folder):
        folder = "".join(folder)
        if folder == "":
            print("cd: missing operand")
            return

        request = dfs_pb2.Path(cwd=self.cwd, filename=folder)
        response = self.stub.cd(request)
        if response.success:
            new_path = os.path.join(self.cwd, folder)
            self.cwd = os.path.normpath(new_path)
        else:
            print(response.response)

    def sl_handler(self, args):
        request = Empty()
        response = self.stub.sl(request)
        for res in response:
            print(res)

    def free_handler(self, args):
        response = self.stub.free(Empty())
        print(response.response)

    def cp_handler(self, args):
        if len(args) < 2:
            print("Usage: cp [SOURCE] [DESTINATION]")
            return

        src = args[0]
        dest = args[1]
        request = dfs_pb2.Path2(cwd=self.cwd, src=src, dest=dest)
        response = self.stub.cp(request)
        if not response.success:
            print(response.response)

    def mv_handler(self, args):
        if len(args) < 2:
            print("Usage: mv [SOURCE] [DESTINATION]")
            return

        src = args[0]
        dest = args[1]
        request = dfs_pb2.Path2(cwd=self.cwd, src=src, dest=dest)
        response = self.stub.mv(request)
        if not response.success:
            print(response.response)

    def init_handler(self, args):
        yn = input("All existing data will be lost. Continue? (y/n) ")
        if yn not in {'Y', 'y', 'yes'}:
            return

        response = self.stub.init(Empty())
        if not response.success:
            print(response.response)
        else:
            response = self.stub.free(Empty())
            print(response.response)

    def rm_handler(self, args):
        filename = "".join(args)
        if filename == "":
            print("rm: missing operand")
            return

        request = dfs_pb2.Path(cwd=self.cwd, filename=filename)
        response = self.stub.info(request)
        if response.success:
            file_attrs = json.loads(response.response)

            if file_attrs["is_dir"]:
                yn = input("{} is a directory. Continue? (y/n) ".format(filename))
                if yn not in {'Y', 'y', 'yes'}:
                    return

            response = self.stub.rm(request)
            if not response.success:
                print(response.response)

        else:
            print("rm: " + response.response)

    def write_handler(self, args):
        if len(args) < 2:
            print("Usage: write [LOCAL] [REMOTE]")
            return

        local = args[0]
        remote = args[1]

        request = Empty()
        response = self.stub.sl(request)
        success = False
        for res in response:
            host_str = "{}:{}".format(res.ip_address, res.port)
            try:
                channel = grpc.insecure_channel(host_str)
                stub = dfs_pb2_grpc.DFS_StorageServerStub(channel)
                chunks_generator = get_file_chunks(self.cwd, local, remote)
                response = stub.write(chunks_generator)
                print(response)
                success = True
                channel.close()
                break
            except Exception as e:
                print("Error connecting to {}: {}".format(host_str, e))

        if success:
            print("Write finished.")
        else:
            print("Write failed.")

    def unknown_handler(self, args):
        print("Unknown command")

    def cmd_handler(self, command):
        cmd_list = command.split()
        command = cmd_list[0]
        args = cmd_list[1:]

        handler = self.command_handlers.get(command, self.unknown_handler)
        try:
            handler(args)
        except Exception as e:
            print(e)


def get_file_chunks(cwd, filename, fake_path):
    path = dfs_pb2.Path(cwd=cwd, filename=fake_path)
    c = 1
    with open(filename, 'rb') as f:
        while True:
            piece = f.read(CHUNK_SIZE)
            if len(piece) == 0:
                return
            yield dfs_pb2.Chunk(Content=piece, chunk_n=c, path=path if c == 1 else None)
            c += 1


def main():
    if len(sys.argv) < 2:
        print("NS address is missing")
        exit(1)

    server_address = sys.argv[1]
    session = Session(server_address)

    while True:
        cmd = input("[DFS {}]$ ".format(session.cwd))

        if cmd == '':
            continue

        session.cmd_handler(cmd)


if __name__ == '__main__':
    main()
