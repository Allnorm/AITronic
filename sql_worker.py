import json
import sqlite3


class SQLWrapper:

    def __init__(self, dbname):
        self.dbname = dbname

    def __enter__(self):
        self.sqlite_connection = sqlite3.connect(self.dbname)
        self.cursor = self.sqlite_connection.cursor()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not exc_type:
            self.sqlite_connection.commit()
        self.cursor.close()
        self.sqlite_connection.close()


class SqlWorker:
    dbname = "database.db"

    def __init__(self):

        sqlite_connection = sqlite3.connect(self.dbname)
        cursor = sqlite_connection.cursor()
        cursor.execute("""CREATE TABLE if not exists chats (
                            chat_id TEXT NOT NULL PRIMARY KEY, 
                            chat_config TEXT NOT NULL, 
                            dialog_text TEXT);""")
        cursor.execute("""CREATE TABLE if not exists templates (
                            chat_id TEXT NOT NULL, 
                            template_name TEXT NOT NULL, 
                            template_data TEXT NOT NULL);""")
        sqlite_connection.commit()
        cursor.close()
        sqlite_connection.close()

    def get_dialog_data(self, chat_id, init_dict=None):
        with SQLWrapper(self.dbname) as sql_wrapper:
            sql_wrapper.cursor.execute("""SELECT * FROM chats WHERE chat_id = ?""", (chat_id,))
            record = sql_wrapper.cursor.fetchall()
            if not record and init_dict:
                parameters = (chat_id, json.dumps(init_dict, ensure_ascii=False), None)
                sql_wrapper.cursor.execute("""INSERT INTO chats VALUES (?,?,?);""", parameters)
                return parameters
            return record[0]

    def dialog_conf_update(self, chat_config, chat_id):
        with SQLWrapper(self.dbname) as sql_wrapper:
            sql_wrapper.cursor.execute("""UPDATE chats SET chat_config = ? where chat_id = ?""",
                                       (json.dumps(chat_config, ensure_ascii=False), chat_id))

    def dialog_update(self, dialog_text, chat_id):
        with SQLWrapper(self.dbname) as sql_wrapper:
            sql_wrapper.cursor.execute("""UPDATE chats SET dialog_text = ? where chat_id = ?""",
                                       (json.dumps(dialog_text, ensure_ascii=False), chat_id))

    def get_templates(self, chat_id, template_name=None):
        with SQLWrapper(self.dbname) as sql_wrapper:
            if template_name:
                sql_wrapper.cursor.execute("""SELECT * FROM templates WHERE chat_id = ? AND template_name = ?""",
                                           (chat_id, template_name))
            else:
                sql_wrapper.cursor.execute("""SELECT * FROM templates WHERE chat_id = ?""", (chat_id,))
            return sql_wrapper.cursor.fetchall()

    def write_template(self, chat_id, template_name, template_data):
        with SQLWrapper(self.dbname) as sql_wrapper:
            sql_wrapper.cursor.execute("""INSERT INTO templates VALUES (?,?,?);""",
                                       (chat_id, template_name,
                                        json.dumps(template_data, ensure_ascii=False)))

    def delete_template(self, chat_id, template_name):
        with SQLWrapper(self.dbname) as sql_wrapper:
            sql_wrapper.cursor.execute("""DELETE FROM templates WHERE chat_id = ? AND template_name = ?""",
                                       (chat_id, template_name))