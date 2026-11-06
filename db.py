                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                added INTEGER,
                games INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        _conn.commit()


def _run(sql, args=(), fetch=None):
    with _lock:
        cur = _conn.execute(sql, args)
        res = None
        if fetch == "one":
            res = cur.fetchone()
        elif fetch == "all":
            res = cur.fetchall()
        _conn.commit()
        return res


# ---------- foydalanuvchilar ----------
def upsert_user(uid, name, username):
    _run(
        """INSERT INTO users(user_id, name, username, joined) VALUES(?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, username=excluded.username""",
        (uid, name, username, int(time.time())),
    )


def get_user(uid):
    return _run("SELECT * FROM users WHERE user_id=?", (uid,), "one")


def add_result(uid, xp, won):
    _run(
        "UPDATE users SET xp=xp+?, games=games+1, wins=wins+? WHERE user_id=?",
        (xp, 1 if won else 0, uid),
    )


def top_users(n=10):
    return _run("SELECT * FROM users WHERE games>0 ORDER BY xp DESC LIMIT ?", (n,), "all")


def user_ids():
    return [r["user_id"] for r in _run("SELECT user_id FROM users", fetch="all")]


def count_users():
    return _run("SELECT COUNT(*) c FROM users", fetch="one")["c"]


def link_ok(uid):
    u = get_user(uid)
    return bool(u and u["link_ok"])


def set_link_ok(uid):
    _run("UPDATE users SET link_ok=1 WHERE user_id=?", (uid,))


def reset_link_ok():
    _run("UPDATE users SET link_ok=0")


# ---------- guruhlar ----------
def upsert_group(chat_id, title):
    _run(
        """INSERT INTO groups(chat_id, title, added) VALUES(?,?,?)
           ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title""",
        (chat_id, title, int(time.time())),
    )


def group_game(chat_id):
    _run("UPDATE groups SET games=games+1 WHERE chat_id=?", (chat_id,))


def count_groups():
    return _run("SELECT COUNT(*) c FROM groups", fetch="one")["c"]


def total_games():
    r = _run("SELECT COALESCE(SUM(games),0) s FROM groups", fetch="one")
    return r["s"]


# ---------- majburiy obunalar ----------
def add_sub(kind, title, chat_id, url):
    _run("INSERT INTO subs(kind,title,chat_id,url) VALUES(?,?,?,?)", (kind, title, chat_id, url))


def list_subs():
    return _run("SELECT * FROM subs ORDER BY id", fetch="all")


def del_sub(sub_id):
    _run("DELETE FROM subs WHERE id=?", (sub_id,))


# ---------- sozlamalar ----------
def get_setting(key, default=None):
    r = _run("SELECT value FROM settings WHERE key=?", (key,), "one")
    return r["value"] if r else default


def set_setting(key, value):
    _run(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
