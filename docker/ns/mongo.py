from pymongo import MongoClient


class DBConnector:
    def __init__(self, username, password, db_address):
        client = MongoClient('mongodb://%s:%s@%s' % (username, password, db_address))
        self.db = client['dfs']

    def get_actual_chunks(self):
        return self.db['chunks'].distinct('chunks')


def main():
    username = "mavl"
    password = "pass"
    client = MongoClient('mongodb://%s:%s@127.0.0.1' % (username, password))
    db = client['dfs']

    attrs = db['attrs']
    chunks = db['chunks']
    # chunks.drop()
    #
    # actual_chunks = attrs.distinct('chunks')
    # for c in actual_chunks:
    #     print(c)
    #     chunks.insert_one({"_id": c, "hosts": []})
    #     print(attrs.find_one({"chunks": c}))
    #print(attrs.find_one({"path": "/testo"}))
    print(chunks.distinct("_id"))


if __name__ == '__main__':
    main()
