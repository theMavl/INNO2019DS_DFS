import dfs_pb2
import dfs_pb2_grpc
import grpc
from concurrent.futures import ThreadPoolExecutor
from google.protobuf.empty_pb2 import Empty
import fs
from fs.walk import Walker
from uuid import uuid4
import time
from threading import Thread
import os
import hashlib
import json
import urllib.request

hosting = os.environ.get('HOSTING', "localhost")

"""
Public address: Fixed port
Private address: Random port
"""

PRIVATE_ADDRESS = None

if hosting == "aws":
    PUBLIC_ADDRESS = urllib.request.urlopen("http://169.254.169.254/latest/meta-data/public-ipv4").read().decode(
        "utf-8")
    PRIVATE_IP = urllib.request.urlopen("http://169.254.169.254/latest/meta-data/local-ipv4").read().decode(
        "utf-8")
elif hosting == "custom" and "PUBLIC_ADDRESS" in os.environ and "PRIVATE_IP" in os.environ:
    PUBLIC_ADDRESS = os.environ['PUBLIC_ADDRESS']
    PRIVATE_IP = os.environ['PRIVATE_IP']
else:
    PUBLIC_ADDRESS = "127.0.0.1:23334"
    PRIVATE_IP = "127.0.0.1"

# Allow for auto assigning port
# PUBLIC_ADDRESS += ":"
PRIVATE_IP += ":"

NS_PUBLIC_ADDRESS = os.environ.get('NS_PUBLIC_ADDRESS', '127.0.0.1:23333')
NS_PRIVATE_ADDRESS = None

if os.path.exists("./SS_UUID"):
    with open("./SS_UUID", "r") as f:
        buf = f.read()
        SS_UUID = buf.strip()
else:
    SS_UUID = str(uuid4())
    with open("./SS_UUID", "w+") as f:
        f.write(SS_UUID)

if not os.path.exists("./storage"):
    os.mkdir("./storage")

FS = fs.open_fs("storage")


# SS_UUID = '97bb4d38-0dc4-46ba-a436-2b3217c2b433'  # str(uuid4())


def ns_link():
    global NS_PUBLIC_ADDRESS
    global NS_PRIVATE_ADDRESS
    global PUBLIC_ADDRESS
    global PRIVATE_ADDRESS

    print("Connecting to", NS_PUBLIC_ADDRESS)

    walker = Walker()
    chunks_list = []
    for path, dirs, files in walker.walk(FS, namespaces=['basic']):
        for file_info in files:
            chunks_list.append(file_info.raw["basic"]["name"])

    chunks_n = len(chunks_list)

    ns_channel = grpc.insecure_channel(NS_PUBLIC_ADDRESS)
    ns_stub = dfs_pb2_grpc.DFS_NamingServerStub(ns_channel)
    request = dfs_pb2.SSLoginInfo(ss_uuid=SS_UUID, exposed_address=PUBLIC_ADDRESS, private_address=PRIVATE_ADDRESS,
                                  chunks_n=chunks_n)
    response = ns_stub.SSLogin(request)
    ns_channel.close()

    if not response.success:
        print("Connected to Naming Server. Pending Sync...")
        print(response.response)
        exit(1)
    else:
        NS_PRIVATE_ADDRESS = response.response

    print(chunks_list)

    def request_messages():
        if chunks_n == 0:
            for i in range(1):
                yield dfs_pb2.SyncChunkUUID(ss_uuid=SS_UUID, chunks_n=0)
        else:
            for c in chunks_list:
                req = dfs_pb2.SyncChunkUUID(ss_uuid=SS_UUID, chunks_n=chunks_n, chunk_uuid=c)
                yield req

    print("Connecting to", NS_PRIVATE_ADDRESS)
    ns_priv_channel = grpc.insecure_channel(NS_PRIVATE_ADDRESS)
    ns_stub = dfs_pb2_grpc.DFS_NSPrivateStub(ns_priv_channel)
    response = ns_stub.SSSync(request_messages())
    for r in response:
        print(r)
        handle_sync_cmd(r)
    ns_priv_channel.close()
    print("Sync Complete.")


def beat():
    global NS_PRIVATE_ADDRESS
    ns_channel = grpc.insecure_channel(NS_PRIVATE_ADDRESS)
    ns_stub = dfs_pb2_grpc.DFS_NSPrivateStub(ns_channel)
    request = dfs_pb2.SSBeatInfo(ss_uuid=SS_UUID)
    print("Heartbeat active")
    while True:
        try:
            response = ns_stub.SSBeat(request)
            if not response.success:
                print("Lost connection to NamingServer. Reconnecting...")
                ns_link()

        except Exception as e:
            print(e)
            print("Lost connection to NamingServer")
            os._exit(1)

        time.sleep(60)


def ss_got_write(path, existing_hash, size, target_chunks, sha1):
    print("{} <-> {}".format(sha1, existing_hash))
    if sha1 == existing_hash:
        print("Version is actual, dismiss chunks")
        for n in target_chunks:
            FS.remove(n)
    else:
        print("SS_GOT:", path, target_chunks)
        ns_channel = grpc.insecure_channel(NS_PRIVATE_ADDRESS)
        ns_stub = dfs_pb2_grpc.DFS_NSPrivateStub(ns_channel)
        filename = os.path.normpath(os.path.join(path.cwd, path.filename))
        ns_request = dfs_pb2.SSGotWriteInfo(ss_uuid=SS_UUID, path=filename, size=size, hash=sha1, chunks=target_chunks)
        ns_response = ns_stub.SSGotWrite(ns_request)
        print(ns_response)


def get_chunk(chunk_UUID, hosts):
    chunk_found = False

    if type(hosts) is not list:
        hosts = [hosts]

    host = None
    for h in hosts:
        host = h
        # print("Request {} from {}".format(chunk_UUID, host))
        channel = None
        try:
            channel = grpc.insecure_channel(host)
            stub = dfs_pb2_grpc.DFS_SSPrivateStub(channel)
            response = stub.has(dfs_pb2.ChunkUUID(chunk_uuid=chunk_UUID))
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
    stub = dfs_pb2_grpc.DFS_SSPrivateStub(channel)
    response = stub.get(dfs_pb2.ChunkUUID(chunk_uuid=chunk_UUID))

    with open(os.path.join("./storage", chunk_UUID), 'wb+') as f:
        for r in response:
            f.write(r.Content)

    channel.close()
    return True


def handle_sync_cmd(request):
    if request.CMD == dfs_pb2.UpdateCMD.get:
        if FS.exists(request.chunk_uuid):
            print("Chunk {} exists".format(request.chunk_uuid))
        else:
            chunk_uuid = request.chunk_uuid
            ns_channel = grpc.insecure_channel(NS_PRIVATE_ADDRESS)
            ns_stub = dfs_pb2_grpc.DFS_NSPrivateStub(ns_channel)
            request = dfs_pb2.ChunkUUID(chunk_uuid=chunk_uuid)
            responses = ns_stub.which_chunk(request)

            hosts = []
            for r in responses:
                h_str = "{}:{}".format(r.ip_address, r.port)
                hosts.append(h_str)

            # print("DOWNLOADING {}".format(chunk_uuid))
            get_chunk(chunk_uuid, hosts)
            request = dfs_pb2.SyncChunkUUID(chunk_uuid=chunk_uuid, ss_uuid=SS_UUID)
            ns_stub.SSGotChunk(request)
            ns_channel.close()

    elif request.CMD == dfs_pb2.UpdateCMD.remove:
        if FS.exists(request.chunk_uuid):
            FS.remove(request.chunk_uuid)
            print("Chunk {} removed".format(request.chunk_uuid))


class DFS_StorageServerServicer(dfs_pb2_grpc.DFS_StorageServerServicer):
    def write(self, request_iterator, context):
        print("{}: write".format(context.peer()))
        first_processed = False
        target_chunks = []
        size = 0
        tmp_c = str(uuid4())
        c = 0
        sha1 = hashlib.sha1()
        path = None
        existing_hash = None

        for request in request_iterator:
            if request.chunk_n == 1:
                print("Got first block")
                first_processed = True
                path = request.path
                print("write {} {}{}".format(context.peer(), path.cwd, path.filename))

                ns_channel = grpc.insecure_channel(NS_PUBLIC_ADDRESS)
                ns_stub = dfs_pb2_grpc.DFS_NamingServerStub(ns_channel)
                ns_response = ns_stub.touch(path)

                if not ns_response.success:
                    print("Touch failed, cancelling request")
                    print(ns_response.response)
                    context.cancel()
                    return dfs_pb2.GenericResponse(success=False, response=ns_response.response)

                existing_hash = ns_response.hash
                print("Touch successful")
                ouf = open("./storage/" + tmp_c, "wb+")
                target_chunks.append(tmp_c)
                ouf.write(request.Content)
                size += len(request.Content)
                sha1.update(request.Content)
                ouf.close()
                c += 1
            else:
                if not first_processed:
                    print("First block not found")
                    context.cancel()
                    return dfs_pb2.GenericResponse(success=False, response="First Block not found")

                tmp_c = str(uuid4())
                ouf = open("./storage/" + tmp_c, "wb+")
                target_chunks.append(tmp_c)
                ouf.write(request.Content)
                size += len(request.Content)
                sha1.update(request.Content)
                c += 1

        print("Stored as", target_chunks)
        ss_got_write_thread = Thread(target=ss_got_write,
                                     args=(path, existing_hash, size, target_chunks, sha1.hexdigest()))
        ss_got_write_thread.start()
        return dfs_pb2.GenericResponse(success=True)

    def get(self, request, context):
        print("{}: get {}".format(context.peer(), request.chunk_uuid))
        real_path = FS.getospath(request.chunk_uuid)
        with open(real_path, 'rb') as content_file:
            content = content_file.read()

        yield dfs_pb2.Chunk(Content=content)

    def has(self, request, context):
        print("{}: has {}".format(context.peer(), request.chunk_uuid))
        return dfs_pb2.GenericResponse(success=FS.exists(request.chunk_uuid))


class DFS_SSPrivateServicer(dfs_pb2_grpc.DFS_SSPrivateServicer):
    def Sync(self, request_iterator, context):
        print("{}: Sync".format(context.peer()))
        for request in request_iterator:
            handle_sync_cmd(request)

        return Empty()

    def has(self, request, context):
        print("{}: has {}".format(context.peer(), request.chunk_uuid))
        return dfs_pb2.GenericResponse(success=FS.exists(request.chunk_uuid))

    def get(self, request, context):
        print("{}: get {}".format(request.chunk_uuid, request.chunk_uuid))
        real_path = FS.getospath(request.chunk_uuid)
        with open(real_path, 'rb') as content_file:
            content = content_file.read()

        yield dfs_pb2.Chunk(Content=content)

    def Nuke(self, request, context):
        print("{}: Nuke".format(context.peer()))
        print("Pending Nuke...")
        for path in FS.walk.files():
            FS.remove(path)
            print("Chunk {} nuked".format(path))

        return Empty()


def main():
    global PUBLIC_ADDRESS
    global PRIVATE_IP
    global PRIVATE_ADDRESS

    fake_public_address = PUBLIC_ADDRESS.split(':')
    fake_public_address[0] = "0.0.0.0"
    fake_public_address = ":".join(fake_public_address)

    server_public = grpc.server(ThreadPoolExecutor())
    dfs_pb2_grpc.add_DFS_StorageServerServicer_to_server(DFS_StorageServerServicer(), server_public)
    server_public.add_insecure_port(fake_public_address)  # PUBLIC_ADDRESS)

    server_private = grpc.server(ThreadPoolExecutor())
    dfs_pb2_grpc.add_DFS_SSPrivateServicer_to_server(DFS_SSPrivateServicer(), server_private)
    private_port = server_private.add_insecure_port(PRIVATE_IP)
    PRIVATE_ADDRESS = PRIVATE_IP + str(private_port)

    ns_link()

    print("Start public storage server {} at {}".format(SS_UUID, PUBLIC_ADDRESS))
    server_public.start()
    print("Start private storage server {} at {}".format(SS_UUID, PRIVATE_ADDRESS))
    server_private.start()

    beat_thread = Thread(target=beat)
    beat_thread.daemon = True
    beat_thread.start()

    server_public.wait_for_termination()
    server_private.wait_for_termination()


if __name__ == '__main__':
    main()
