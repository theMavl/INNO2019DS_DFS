python -m grpc_tools.protoc -I./protos --python_out=. --grpc_python_out=. ./protos/dfs.proto
cp dfs_pb2_grpc.py client/dfs_pb2_grpc.py
cp dfs_pb2.py client/dfs_pb2.py
cp dfs_pb2_grpc.py docker/ns/dfs_pb2_grpc.py
cp dfs_pb2.py docker/ns/dfs_pb2.py
cp dfs_pb2_grpc.py docker/ss/dfs_pb2_grpc.py
cp dfs_pb2.py docker/ss/dfs_pb2.py
rm dfs_pb2_grpc.py
rm dfs_pb2.py
