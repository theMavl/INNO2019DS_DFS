import dfs_pb2
import dfs_pb2_grpc
import grpc
from concurrent.futures import ThreadPoolExecutor
import fs
import fs.errors
import os
from mongo import DBConnector
import json
import time
from threading import Thread
from pydispatch import dispatcher
from bson.objectid import ObjectId
from google.protobuf.empty_pb2 import Empty
import shutil
import urllib.request

# ------------------------- Global Section -------------------------

hosting = os.environ.get('HOSTING', "localhost")

"""
aws-many:
    PRIVATE_PORT, PUBLIC_PORT
    
aws-single:
    PRIVATE_ADDRESS, PUBLIC_PORT
    
custom:
    PRIVATE_ADDRESS, PUBLIC_ADDRESS
"""

if hosting == "aws-single" and "PRIVATE_ADDRESS" in os.environ:
    PUBLIC_IP = urllib.request.urlopen("http://169.254.169.254/latest/meta-data/public-ipv4").read().decode("utf-8")
    PRIVATE_ADDRESS = os.environ['PRIVATE_ADDRESS']
    PUBLIC_PORT = os.environ.get('PUBLIC_PORT', 23333)
    PUBLIC_ADDRESS = '{}:{}'.format(PUBLIC_IP, PUBLIC_PORT)
elif hosting == "aws-many" and "PRIVATE_PORT" in os.environ:
    PUBLIC_IP = urllib.request.urlopen("http://169.254.169.254/latest/meta-data/public-ipv4").read().decode("utf-8")
    PRIVATE_IP = urllib.request.urlopen("http://169.254.169.254/latest/meta-data/local-ipv4").read().decode("utf-8")
    PUBLIC_PORT = os.environ.get('PUBLIC_PORT', 23333)
    PRIVATE_PORT = os.environ.get('PRIVATE_PORT', 33333)
    PUBLIC_ADDRESS = '{}:{}'.format(PUBLIC_IP, PUBLIC_PORT)
    PRIVATE_ADDRESS = '{}:{}'.format(PRIVATE_IP, PRIVATE_PORT)
elif hosting == "custom" and "PUBLIC_ADDRESS" in os.environ and "PRIVATE_ADDRESS" in os.environ:
    PUBLIC_ADDRESS = os.environ['PUBLIC_ADDRESS']
    PRIVATE_ADDRESS = os.environ['PRIVATE_ADDRESS']
else:
    PUBLIC_ADDRESS = "127.0.0.1:23333"
    PRIVATE_ADDRESS = "127.0.0.1:33333"

if not os.path.exists("./fs"):
    os.mkdir("./fs")

FS = fs.open_fs("fs")

username = "mavl"
password = "pass"
db_address = "mongo"
DB = DBConnector(username, password, db_address)
ATTRS = DB.db["attrs"]
CHUNKS = DB.db["chunks"]

PENDING_SYNC_STORAGES = {}
STORAGES = {}

SIGNAL_BROADCAST_UPDATE = "broadcast_update"
SIGNAL_BROADCAST_NUKE = "broadcast_nuke"


# ------------------------- Misc Functions Section -------------------------

def ss_watcher():
    print("SS Watcher Active")
    while True:
        time.sleep(60)
        storages = list(STORAGES.keys())
        for s in storages:
            # print(STORAGES, s)
            last_beat_delta = time.time() - STORAGES[s]["last_beat"]
            if last_beat_delta > 5 * 60:
                print("Storage {} ({}) didn't beat in {} mins. Assuming it is dead".format(STORAGES[s]["address"], s,
                                                                                           last_beat_delta // 60))
                STORAGES.pop(s)
                chunks = CHUNKS.find({"hosts": s})
                for chunk in chunks:
                    CHUNKS.update_one(chunk, {"$pull": {"hosts": s}})
            else:
                print("SS-WATCH: {}: {}".format(s, last_beat_delta))


def init_chunks_index():
    CHUNKS.drop()

    actual_chunks = ATTRS.distinct('chunks')
    for c in actual_chunks:
        CHUNKS.insert_one({"_id": c, "hosts": []})


def chunks_update_generator(chunks, command):
    for chunk in chunks:
        response = dfs_pb2.Update(CMD=command, chunk_uuid=chunk)
        yield response


def broadcast_update(chunks, command):
    # dfs_pb2.UpdateCMD.get / dfs_pb2.UpdateCMD.remove
    for s in STORAGES:
        address = STORAGES[s]["private_address"]
        channel = grpc.insecure_channel(address)
        stub = dfs_pb2_grpc.DFS_SSPrivateStub(channel)
        cmd_generator = chunks_update_generator(chunks, command)
        response = stub.Sync(cmd_generator)
        print(response)
        channel.close()


def broadcast_nuke():
    for s in STORAGES:
        address = STORAGES[s]["private_address"]
        channel = grpc.insecure_channel(address)
        stub = dfs_pb2_grpc.DFS_SSPrivateStub(channel)
        response = stub.Nuke(Empty())
        channel.close()


def get_fileattr_id(filesystem, fake_path):
    real_path = str(filesystem.getsyspath(fake_path))
    print("get_fileattr_id {} {} {}".format(fake_path, real_path, os.path.exists(real_path)))
    with open(real_path, "r") as f:
        lines = f.readlines()
    return lines[0].strip()


# ------------------------- Servicers Section -------------------------

class DFS_NamingServerServicer(dfs_pb2_grpc.DFS_NamingServerServicer):
    def init(self, request, context):
        print("{}: init".format(context.peer()))
        CHUNKS.drop()
        ATTRS.drop()
        # shutil.rmtree('./fs')
        # os.mkdir('./fs')

        for filename in os.listdir("./fs"):
            file_path = os.path.join("./fs", filename)
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)

        dispatcher.send(signal=SIGNAL_BROADCAST_NUKE)
        response = dfs_pb2.GenericResponse(
            success=True,
            response="FS flushed"
        )
        return response

    def ls(self, request, context):
        req_path = os.path.normpath(os.path.join(request.cwd, request.filename))
        print("{}: ls {}".format(context.peer(), req_path))
        response = dfs_pb2.GenericResponse(
            success=True,
            response=str(FS.listdir(req_path)))
        return response

    def sl(self, request, context):
        print("{}: sl".format(context.peer()))
        print(STORAGES.values())
        for s in STORAGES.values():
            ss_addr = s["address"].split(":")
            response = dfs_pb2.SSSummary(
                ip_address=ss_addr[0],
                port=int(ss_addr[1]))
            yield response

    def info(self, request, context):
        req_path = os.path.normpath(os.path.join(request.cwd, request.filename))
        print("{}: info {}".format(context.peer(), req_path))
        if not FS.exists(req_path):
            success = False
            response = "info: Resource {} not found".format(request.filename)
        elif FS.isdir(req_path):
            success = True
            response = json.dumps(FS.getinfo(req_path).raw["basic"])
        else:
            file_attr_id = get_fileattr_id(FS, req_path)
            print("Look for", file_attr_id)
            file_attrs = ATTRS.find_one({"_id": ObjectId(file_attr_id)})
            # file_attrs = ATTRS.find_one({"path": req_path})
            print(file_attrs, req_path)
            if file_attrs:
                attrs = FS.getinfo(req_path).raw["basic"]
                file_attrs.pop("chunks")
                file_attrs.pop("_id")
                attrs.update(file_attrs)
                success = True
                response = json.dumps(attrs)
            else:
                success = False
                response = "info: Resource {} not found".format(request.filename)

        return dfs_pb2.GenericResponse(
            success=success,
            response=response
        )

    def cd(self, request, context):
        print(request)
        req_path = os.path.normpath(os.path.join(request.cwd, request.filename))
        print("{}: cd {}".format(context.peer(), req_path))
        if not FS.exists(req_path):
            success = False
            response = "cd: {}: No such file or directory".format(request.filename)
        elif not FS.isdir(req_path):
            success = False
            response = "cd: {}: Not a directory".format(request.filename)
        else:
            success = True
            response = "OK"

        print(success, response)

        return dfs_pb2.GenericResponse(
            success=success,
            response=response
        )

    def which(self, request, context):
        req_path = os.path.normpath(os.path.join(request.cwd, request.filename))
        print("{}: which {}".format(context.peer(), req_path))
        if FS.isdir(req_path):
            success = False
            response = "which: Is a directory: {}".format(request.filename)
        elif not FS.exists(req_path):
            success = False
            response = "which: Resource {} not found".format(request.filename)
        else:
            file_attr_id = get_fileattr_id(FS, req_path)
            file_attrs = ATTRS.find_one({"_id": ObjectId(file_attr_id)})
            # file_attrs = ATTRS.find_one({"path": req_path})
            print(file_attrs, req_path)

            if file_attrs is None:
                success = False
                response = "which: Resource {} not found".format(request.filename)
            else:
                chunks = file_attrs["chunks"]
                hash_str = file_attrs["hash"]
                ret_chunks = {}

                for chunk in chunks:
                    hosts = CHUNKS.find_one({"_id": chunk})["hosts"]
                    host_addrs = []
                    for h in hosts:
                        if h in STORAGES:
                            host_addrs.append(STORAGES[h]["address"])
                    ret_chunks.update({chunk: host_addrs})

                success = True
                ret_json = {"hash": hash_str, "chunks": ret_chunks}
                response = json.dumps(ret_json)

        return dfs_pb2.GenericResponse(
            success=success,
            response=response)

    def which_chunk(self, request, context):
        chunk_uuid = request.chunk_uuid
        print("{}: which_chunk {}".format(context.peer(), chunk_uuid))
        chunk_info = CHUNKS.find_one({"_id": chunk_uuid})

        if chunk_info:
            for host in chunk_info["hosts"]:
                h = STORAGES[host]
                ss_addr = h["address"].split(":")
                response = dfs_pb2.SSSummary(
                    ip_address=ss_addr[0],
                    port=int(ss_addr[1]))
                yield response
        else:
            print("Chunk UUID {} not found".format(chunk_uuid))

    def mkdir(self, request, context):
        req_path = os.path.normpath(os.path.join(request.cwd, request.filename))
        print("{}: mkdir {}".format(context.peer(), req_path))

        if FS.exists(req_path):
            success = False
            response = 'mkdir: cannot create directory {}: File exists'.format(request.filename)
        else:
            try:
                FS.makedir(req_path)
                success = True
                response = req_path
            except Exception as e:
                success = False
                response = str(e)

        return dfs_pb2.GenericResponse(
            success=success,
            response=response)

    def touch(self, request, context):
        req_path = os.path.normpath(os.path.join(request.cwd, request.filename))
        print("{}: touch {}".format(context.peer(), req_path))
        hash_str = None
        response = None
        if FS.exists(req_path):
            exists = True
            if FS.isdir(req_path):
                success = False
                response = "touch: is an existing directory"
            else:
                file_attr_id = get_fileattr_id(FS, req_path)
                print("Looking for", file_attr_id)
                file_attrs = ATTRS.find_one({"_id": ObjectId(file_attr_id)})
                # file_attrs = ATTRS.find_one({"path": req_path})
                if file_attrs:
                    hash_str = file_attrs.pop("hash", None)
                    success = True
                else:
                    print("File {} exists in FS but not in Cache!".format(req_path))
                    ins = ATTRS.insert_one({"hash": None, "size": 0, "chunks": []})
                    with open(FS.getospath(req_path), "w") as f:
                        f.write(str(ins.inserted_id))
                    success = True

        else:
            FS.touch(req_path)
            ins = ATTRS.insert_one({"hash": None, "size": 0, "chunks": []})
            # print(ins.inserted_id)
            with open(FS.getospath(req_path), "w") as f:
                f.write(str(ins.inserted_id))
            success = True
            exists = False
            response = req_path

        return dfs_pb2.TouchResult(
            success=success,
            response=response,
            exists=exists,
            hash=hash_str)

    def mv(self, request, context):
        path_src = os.path.normpath(os.path.join(request.cwd, request.src))
        path_dest = os.path.normpath(os.path.join(request.cwd, request.dest))
        print("{}: mv {} {}".format(context.peer(), path_src, path_dest))
        response = None
        if FS.exists(path_src):
            if FS.exists(path_dest):
                success = False
                response = "mv: '{}': File exists".format(request.dest)
            else:
                if FS.isdir(path_src):
                    FS.movedir(path_src, path_dest)
                    success = True
                else:
                    FS.move(path_src, path_dest)
                    success = True
        else:
            success = False
            response = "mv: '{}': No such file or directory".format(request.src)

        return dfs_pb2.GenericResponse(
            success=success,
            response=response)

    def cp(self, request, context):
        path_src = os.path.normpath(os.path.join(request.cwd, request.src))
        path_dest = os.path.normpath(os.path.join(request.cwd, request.dest))
        print("{}: cp {} {}".format(context.peer(), path_src, path_dest))
        response = None
        if FS.exists(path_src):
            if FS.exists(path_dest):
                success = False
                response = "cp: '{}': File exists".format(request.dest)
            else:
                if FS.isdir(path_src):
                    success = False
                    response = "cp: '{}': Is a directory".format(request.dest)
                else:
                    file_attr_id = get_fileattr_id(FS, path_src)
                    print("Looking for", file_attr_id)
                    file_attrs = ATTRS.find_one({"_id": ObjectId(file_attr_id)})
                    if file_attrs:
                        FS.copy(path_src, path_dest)
                        new_id = ObjectId()
                        file_attrs["_id"] = new_id
                        ATTRS.insert_one(file_attrs)
                        with open(FS.getospath(path_dest), "w") as f:
                            f.write(str(new_id))
                        success = True
                    else:
                        success = False
                        response = "cp: '{}': Internal error: No attrs in cache.".format(request.dest)
        else:
            success = False
            response = "cp: '{}': No such file or directory".format(request.src)

        return dfs_pb2.GenericResponse(
            success=success,
            response=response)

    def rm(self, request, context):
        req_path = os.path.normpath(os.path.join(request.cwd, request.filename))
        print("{}: rm {}".format(context.peer(), req_path))
        response = None
        success = False

        if FS.exists(req_path):
            if FS.isdir(req_path):
                try:
                    sub_fs = FS.opendir(req_path)
                    for path in sub_fs.walk.files():
                        file_attr_id = get_fileattr_id(sub_fs, str(path))
                        print("Looking for", file_attr_id)
                        file_attr = ATTRS.find_one({"_id": ObjectId(file_attr_id)})
                        unused_chunks = []
                        if file_attr:
                            old_chunks = file_attr["chunks"]
                            for chunk in old_chunks:
                                at = ATTRS.find({"chunks": chunk})
                                if at.count() == 1:  # In case if file was copied and chunks are used in copy
                                    CHUNKS.delete_one({"_id": chunk})
                                    unused_chunks.append(chunk)

                            dispatcher.send(chunks=unused_chunks, command=dfs_pb2.UpdateCMD.remove,
                                            signal=SIGNAL_BROADCAST_UPDATE)

                        ATTRS.delete_one({"_id": ObjectId(file_attr_id)})

                    try:
                        FS.removedir(req_path)
                        success = True
                    except fs.errors.DirectoryNotEmpty:
                        shutil.rmtree(FS.getsyspath(req_path))
                        success = True
                    except fs.errors.RemoveRootError as e:
                        response = "rm: {}".format(e)
                        success = False

                except Exception as e:
                    print(e)
                    success = False
                    response = "rm: {}".format(e)
            else:
                file_attr_id = get_fileattr_id(FS, str(req_path))
                print("Looking for", file_attr_id)
                ATTRS.delete_one({"_id": ObjectId(file_attr_id)})
                FS.remove(req_path)
                success = True
        else:
            success = False
            response = "rm: '{}': No such file or directory".format(request.src)

        return dfs_pb2.GenericResponse(
            success=success,
            response=response)

    def free(self, request, context):
        print("{}: free".format(context.peer()))
        min_free = 999999999
        for s in STORAGES:
            try:
                address = STORAGES[s]["private_address"]
                channel = grpc.insecure_channel(address)
                stub = dfs_pb2_grpc.DFS_SSPrivateStub(channel)
                response = stub.free(Empty())
                if response.free < min_free:
                    min_free = response.free
                channel.close()
            except Exception as e:
                print("free:", e)

        return dfs_pb2.GenericResponse(
            success=True,
            response="Free space: {}".format(min_free))


class DFS_NSPrivateServicer(dfs_pb2_grpc.DFS_NSPrivateServicer):
    def SSLogin(self, request, context):
        ss_address = context.peer()[5:]
        if ss_address[:5] == "[::1]":
            tmp = ss_address.split(":")
            ss_address = "127.0.0.1:" + tmp[-1]

        print("{}: SSLogin".format(context.peer()))
        addr = request.exposed_address
        priv_addr = request.private_address
        try:
            test_channel = grpc.insecure_channel(addr)
            test_channel.close()
            print("Destination {} alive".format(addr))
            test_channel = grpc.insecure_channel(priv_addr)
            test_channel.close()
            print("Destination {} alive".format(priv_addr))
            response = dfs_pb2.GenericResponse(success=True, response=str(PRIVATE_ADDRESS))

            # If connected with the same uuid but different ip, delete everything related to previous ip
            if request.ss_uuid in STORAGES:
                chunks = CHUNKS.find({"hosts": request.ss_uuid})
                for chunk in chunks:
                    CHUNKS.update_one(chunk, {"$pull": {"hosts": request.ss_uuid}})

            PENDING_SYNC_STORAGES.update({request.ss_uuid: {"address": addr, "private_address": priv_addr}})
            print("Storage {} ({}) added to pending sync list".format(addr, request.ss_uuid))

        except Exception as e:
            print(e)
            response = dfs_pb2.GenericResponse(success=False, response="Destination unreachable")

        return response

    def SSGotChunk(self, request, context):
        ss_uuid = request.ss_uuid
        chunk_uuid = request.chunk_uuid
        print("{}: SSGotChunk {} ({} chunks)".format(context.peer(), ss_uuid, chunk_uuid))
        if ss_uuid in STORAGES:
            a = CHUNKS.update_one({"_id": chunk_uuid}, {'$addToSet': {'hosts': ss_uuid}})
            if a.modified_count > 0:
                print("Chunk {} got new replica at {}".format(chunk_uuid, ss_uuid))
            else:
                print("Chunk {} not found".format(request.chunk_uuid))

        return Empty()

    def SSSync(self, request_iterator, context):
        requests = [x for x in request_iterator]
        first = requests[0]
        ss_uuid = first.ss_uuid
        chunks_n = first.chunks_n
        print("{}: SSSync {} ({} chunks)".format(context.peer(), ss_uuid, chunks_n))

        if ss_uuid in PENDING_SYNC_STORAGES or ss_uuid in STORAGES:
            actual_chunks = set(ATTRS.distinct("chunks"))

            if chunks_n == 0:
                ss_chunks = set()
            else:
                ss_chunks = set([x.chunk_uuid for x in requests])

            missing_chunks = actual_chunks - ss_chunks
            junk_chunks = ss_chunks - actual_chunks

            for c in ss_chunks:
                if c in actual_chunks:
                    upd = CHUNKS.update_one({"_id": c}, {'$addToSet': {'hosts': ss_uuid}})

            if len(missing_chunks) == 0 and len(junk_chunks) == 0:
                print("Storage has actual information")
                addresses = PENDING_SYNC_STORAGES.pop(first.ss_uuid)
                STORAGES.update({first.ss_uuid: {"address": addresses["address"],
                                                 "private_address": addresses["private_address"],
                                                 "last_beat": time.time()}})
                return dfs_pb2.Update(CMD=dfs_pb2.UpdateCMD.OK)
            else:
                print("Missing:", missing_chunks)
                print("Junk:", junk_chunks)

            chunk_hosts = {}
            for c in missing_chunks:
                chunk_info = CHUNKS.find_one({"_id": c})
                if chunk_info:
                    chunk_hosts.update({c: chunk_info["hosts"]})

            remove_cmds = [(dfs_pb2.UpdateCMD.remove, c, "") for c in junk_chunks]
            get_cmds = [(dfs_pb2.UpdateCMD.get, c, chunk_hosts[c]) for c in missing_chunks if len(chunk_hosts[c]) > 0]
            commands = remove_cmds + get_cmds

            print("Prepared commands:", commands)

            addresses = PENDING_SYNC_STORAGES.pop(first.ss_uuid)
            STORAGES.update({first.ss_uuid: {"address": addresses["address"],
                                             "private_address": addresses["private_address"],
                                             "last_beat": time.time()}})
            print("Storage {} ({}) added to active list".format(addresses["address"], first.ss_uuid))

            for c in commands:
                print(c[0], c[1], c[2])
                try:
                    response = dfs_pb2.Update(CMD=c[0], chunk_uuid=c[1], hosts=c[2])
                    print(response)
                    yield response
                except Exception as e:
                    print(e)
        else:
            return dfs_pb2.Update(CMD=dfs_pb2.UpdateCMD.Error)

    def SSGotWrite(self, request, context):
        print("{}: SSGotWrite {} {}".format(context.peer(), request.ss_uuid, request.path))
        ss_uuid = request.ss_uuid
        target_file = request.path
        size = request.size
        chunks = list(request.chunks)
        hash_str = request.hash

        file_attr_id = get_fileattr_id(FS, target_file)
        print("Looking for", file_attr_id)
        file_attrs = ATTRS.find_one({"_id": ObjectId(file_attr_id)})

        if not file_attrs:
            print("File {} is not in Cache!".format(target_file))

        # old_chunks = ATTRS.find_one({"path": target_file})

        if file_attrs and "chunks" in file_attrs:
            old_chunks = file_attrs["chunks"]
            unused_chunks = []

            for chunk in old_chunks:
                at = ATTRS.find({"chunks": chunk})
                if at.count() == 1:  # In case if file was copied and chunks are used in copy
                    CHUNKS.delete_one({"_id": chunk})
                    unused_chunks.append(chunk)

            dispatcher.send(chunks=unused_chunks, command=dfs_pb2.UpdateCMD.remove, signal=SIGNAL_BROADCAST_UPDATE)

        ATTRS.update_one({"_id": ObjectId(file_attr_id)}, {'$set': {
            'size': size,
            'hash': hash_str,
            'chunks': chunks
        }
        })

        for chunk in chunks:
            CHUNKS.insert_one({"_id": chunk, "hosts": [ss_uuid]})

        dispatcher.send(chunks=chunks, command=dfs_pb2.UpdateCMD.get, signal=SIGNAL_BROADCAST_UPDATE)

        return dfs_pb2.GenericResponse(success=True, response="OK")

    def SSBeat(self, request, context):
        print("{}: beat".format(context.peer()))
        ss_uuid = request.ss_uuid
        if ss_uuid in STORAGES:
            storage_info = STORAGES.get(ss_uuid)
            storage_info.update({"last_beat": time.time()})
            return dfs_pb2.GenericResponse(success=True, response="OK")
        else:
            return dfs_pb2.GenericResponse(success=False, response="Who the hell are you?")

    def touch(self, request, context):
        req_path = os.path.normpath(os.path.join(request.cwd, request.filename))
        print("{}: touch {}".format(context.peer(), req_path))
        hash_str = None
        response = None
        if FS.exists(req_path):
            exists = True
            if FS.isdir(req_path):
                success = False
                response = "touch: is an existing directory"
            else:
                file_attr_id = get_fileattr_id(FS, req_path)
                print("Looking for", file_attr_id)
                file_attrs = ATTRS.find_one({"_id": ObjectId(file_attr_id)})
                # file_attrs = ATTRS.find_one({"path": req_path})
                if file_attrs:
                    hash_str = file_attrs.pop("hash", None)
                    success = True
                else:
                    print("File {} exists in FS but not in Cache!".format(req_path))
                    ins = ATTRS.insert_one({"hash": None, "size": 0, "chunks": []})
                    with open(FS.getospath(req_path), "w") as f:
                        f.write(str(ins.inserted_id))
                    success = True

        else:
            FS.touch(req_path)
            ins = ATTRS.insert_one({"hash": None, "size": 0, "chunks": []})
            # print(ins.inserted_id)
            with open(FS.getospath(req_path), "w") as f:
                f.write(str(ins.inserted_id))
            success = True
            exists = False
            response = req_path

        return dfs_pb2.TouchResult(
            success=success,
            response=response,
            exists=exists,
            hash=hash_str)

    def which_chunk(self, request, context):
        chunk_uuid = request.chunk_uuid
        chunk_info = CHUNKS.find_one({"_id": chunk_uuid})

        print("{}: which_chunk {}".format(context.peer(), chunk_uuid))
        if chunk_info:
            for host in chunk_info["hosts"]:
                h = STORAGES[host]
                ss_addr = h["private_address"].split(":")
                response = dfs_pb2.SSSummary(
                    ip_address=ss_addr[0],
                    port=int(ss_addr[1]))
                yield response
        else:
            print("Chunk UUID {} not found".format(chunk_uuid))


# ------------------------- Main Section -------------------------

def main():
    init_chunks_index()
    dispatcher.connect(broadcast_update, signal=SIGNAL_BROADCAST_UPDATE)
    dispatcher.connect(broadcast_nuke, signal=SIGNAL_BROADCAST_NUKE)

    fake_public_address = PUBLIC_ADDRESS.split(':')
    fake_public_address[0] = "0.0.0.0"
    fake_public_address = ":".join(fake_public_address)

    fake_private_address = PRIVATE_ADDRESS.split(':')
    fake_private_address[0] = "0.0.0.0"
    fake_private_address = ":".join(fake_private_address)

    server_public = grpc.server(ThreadPoolExecutor())
    dfs_pb2_grpc.add_DFS_NamingServerServicer_to_server(DFS_NamingServerServicer(), server_public)
    server_public.add_insecure_port(fake_public_address)
    print("Start public NS at", PUBLIC_ADDRESS)
    server_public.start()

    server_private = grpc.server(ThreadPoolExecutor())
    dfs_pb2_grpc.add_DFS_NSPrivateServicer_to_server(DFS_NSPrivateServicer(), server_private)
    server_private.add_insecure_port(fake_private_address)
    print("Start private NS at", PRIVATE_ADDRESS)
    server_private.start()

    ss_watcher_thread = Thread(target=ss_watcher)
    ss_watcher_thread.daemon = True
    ss_watcher_thread.start()

    server_public.wait_for_termination()
    server_private.wait_for_termination()


if __name__ == '__main__':
    main()
