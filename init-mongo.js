db.createUser({
	user: "mavl",
	pwd: "pass",
	roles : [
	{
		role: "readWrite",
		db: "dfs"
	}
	]
})
