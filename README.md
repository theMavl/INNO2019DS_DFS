# INNO2019DS_DFS
A Distributed Filesystem

![](https://i.imgur.com/vxeo6gl.png)
![](https://i.imgur.com/HsgztpW.png)


## Communication protocols
Naming Server and Storage Servers have public and private end-points with corresponding API.

1. Naming Server
    1. Public
        ```
        rpc init (Empty) returns (GenericResponse);
        rpc ls (Path) returns (GenericResponse);
        rpc sl (Empty) returns (stream SSSummary);
        rpc cd (Path) returns (GenericResponse);
        rpc rm (Path) returns (GenericResponse);
        rpc cp (Path2) returns (GenericResponse);
        rpc mv (Path2) returns (GenericResponse);
        rpc which (Path) returns (GenericResponse);
        rpc which_chunk (ChunkUUID) returns (stream SSSummary);
        rpc info (Path) returns (GenericResponse);
        rpc mkdir (Path) returns (GenericResponse);
        rpc touch (Path) returns (TouchResult);
        rpc free (Empty) returns (GenericResponse);
        ```
    2. Private
        ```
        rpc touch (Path) returns (TouchResult);
        rpc which_chunk (ChunkUUID) returns (stream SSSummary);
        rpc SSLogin (SSLoginInfo) returns (GenericResponse); 
        rpc SSSync (stream SyncChunkUUID) returns (stream Update);
        rpc SSGotChunk (SyncChunkUUID) returns (Empty);
        rpc SSBeat (SSBeatInfo) returns (GenericResponse);
        rpc SSGotWrite (SSGotWriteInfo) returns (GenericResponse);
        ```
2. Storage Server
    1. Public
        ```
        rpc has (ChunkUUID) returns (GenericResponse);
        rpc get (ChunkUUID) returns (stream Chunk);
        rpc write (stream Chunk) returns (GenericResponse);
        ```
    2. Private
        ```
        rpc Sync (stream Update) returns (Empty);
        rpc get (ChunkUUID) returns (stream Chunk);
        rpc has (ChunkUUID) returns (GenericResponse); 
        rpc Nuke (Empty) returns (Empty);
        rpc free (Empty) returns (FreeSpaceInfo);
        ```

## Installing
1. Determine the ports that are to be used. You need 2 ports for the naming server and 2 ports for the storage server. If one host will have both servers running, these ports can not intersect. 
    For this example, the following port numbers are chosen:
    
    ```
    NS Public - 23333
    NS Private - 33333
    SS Public - 23334
    SS Private - 33334
    ```
    

2. Launch 3 AWS instances. Make sure that all instances are in the same subnet and share the same security group.
3. Check the subnet’s CIDR. In this example, it looks as `172.31.16.0/20`
4. Set the following inbound rules for the security group used by your instances:

    | Type     | Protocol | Port range | Source | Description |
    | -------- | -------- | ---------- | ------ | ----------- |
    |Custom TCP Rule|TCP|2377|Custom - 172.31.16.0/20|Docker Swarm|
    |Custom TCP Rule|TCP|2377|Custom - 172.31.16.0/20|Docker Swarm|
    |Custom TCP Rule|TCP|8080|My IP|Visualizer|
    |Custom TCP Rule|TCP|33334|Custom - 172.31.16.0/20|DFS private storage endpoint| 
    |Custom TCP Rule|TCP|33333|Custom - 172.31.16.0/20|DFS private ns endpoint|
    |Custom TCP Rule|TCP|23334|Anywhere|DFS public storage endpoint| 
    |Custom TCP Rule|TCP|23333|Anywhere|DFS public ns endpoint| 
    |Custom TCP Rule|TCP|8081|My IP|mongo-express|
    |All ICMP - IPv4|ICMP|0-65535|My IP|
    |All ICMP - IPv4|ICMP|0-65535|Custom - 172.31.16.0/20

5. Memorize the public IP address of the node that you want to be the main one. In this example, it is `54.194.98.106`. Also, remember its hostname, i.e. everything from Private DNS before the first dot: `ip-172-31-30-19`
6. SSH to your Name Node
7. Get Docker:
    ```
    $ curl -fsSL https://get.docker.com -o get-docker.sh
    $ sudo sh get-docker.sh
    ```
8. Install docker-compose:
    ```
    $ sudo apt install docker-compose -y
    ```

8. Clone the repository and navigate to the repository folder
    ```
    $ git clone https://github.com/theMavl/INNO2019DS_DFS/
    $ cd INNO2019DS_DFS
    ```

9. Create .env file with the following contents:
    ```
    NS_HOSTNAME=ip-172-31-30-19
    NS_PUBLIC_PORT=23333
    NS_PRIVATE_PORT=33333
    NS_PRIVATE_ADDRESS=172.31.30.19:33333
    SS_PUBLIC_PORT=23334
    SS_PRIVATE_PORT=33334
    ```
    
    See steps 1 and 5

10. Become superuser. Create the following directori`es:
    ```
    $ sudo su
    $ mkdir /var/dfs
    $ mkdir /var/dfs/fs
    $ mkdir /var/dfs/storage
    $ mkdir /var/dfs/config
    $ mkdir /var/dfs/mongo-volume
    ```

11. Initialize swarm
    `$ docker swarm init --advertise-addr 172.31.30.19`
    See step 5
	
12. Join swarm as manager and copy the output
    ```
    $ docker swarm join-token manager
    To add a manager to this swarm, run the following command:

        docker swarm join --token <TOKEN> 172.31.30.19:2377
    ```

13. SSH to the first storage server. Get docker (step 7). Join the swarm using the obtained command from step 13.
14. Become superuser. Create the following directories:
    ```
    $ sudo su
    $ mkdir /var/dfs
    $ mkdir /var/dfs/storage
    $ mkdir /var/dfs/config
    ```

15. Repeat steps 14-15 at the last storage server.
16. Go back to the main node. Deploy the application:
    ```
    $ export $(cat .env | xargs)
    $ docker stack deploy --compose-file docker-compose-dist-deploy.yml dfs
    ```
17. See the services list
    ```
    $ docker service ls
    ID                  NAME                 MODE                REPLICAS            IMAGE                             PORTS
    cyjf8a6mls5q        dfs_mongo            replicated          1/1                 mongo:latest                      
    tqqsmlcy2sga        dfs_mongo-express    replicated          1/1                 mongo-express:latest              *:8081->8081/tcp
    hxxnap9bj247        dfs_naming-server    replicated          1/1                 themavl/dfs-ns:latest             *:23333->23333/tcp, *:33333->33333/tcp
    3u44e70w8g9g        dfs_storage-server   global              3/3                 themavl/dfs-ss:latest             *:23334->23334/tcp, *:33334->33334/tcp
    j85yth9mdhow        dfs_visualizer       replicated          1/1                 dockersamples/visualizer:latest   *:8080->8080/tcp
    ```

Congratulations! DFS is finally deployed and ready to work.

Check how nodes are loaded: http://54.194.98.106:8080/

## Usage
1. Clone the repository to client host
    ```
    $ git clone https://github.com/theMavl/INNO2019DS_DFS/
    $ cd INNO2019DS_DFS/client
    ```
2. Create virtual environment and install necessary packages
    ```
    $ python3 -m venv venv
    $ source venv/bin/activate
    $ pip install -r requirements.txt
    ```
3. Launch client
    `$ python3 dfs_client.py 54.194.98.106:23333`
    
## Client commands
- `init` - Initialize filesystem (destroy everything)
- `ls [PATH]` - list files in current or specified directory
- `cd [PATH]` - move to directory
- `info [FILENAME]` - get information about file
- `which [FILENAME]` - get list of chunks and hosts IP addresses that have corresponding chunks
- `which_chunk [CHUNK_UUID]` - get list of hosts IP addresses that has corresponding chunk
- `mkdir [DIRNAME]` - create directory
- `sl` - list of active storage servers
- `touch [FILENAME]` - create empty file
- `get [REMOTE] [LOCAL]` - download file
- `cp [SOURCE] [DESTINATION]` - copy file
- `mv [SOURCE] [DESTINATION]` - move file
- `rm [FILENAME]` - remove file/directory
- `write [LOCAL] [REMOTE]` - upload file
- `free` - how many free storage size left
