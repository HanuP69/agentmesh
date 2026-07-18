"""Chat Service — MongoDB-backed chat history CRUD.
Isolated from all other services; only the api-gateway calls this."""
import time
import uuid
import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shared.config import settings

logger = logging.getLogger(__name__)
app = FastAPI(title="AgentMesh Chat Service")


# --- Mongo Store (copied from monolith db/chat_store.py) ---
class _InMemoryStore:
    def __init__(self):
        self.users = {}
        self.conversations = {}
        self.messages = {}

    def upsert_user(self, sub, email, name, picture):
        user = self.users.get(sub, {"user_id": sub})
        user.update({"email": email, "name": name, "picture": picture, "updated_at": time.time()})
        self.users[sub] = user
        return user

    def get_user(self, sub):
        return self.users.get(sub)

    def create_conversation(self, user_id, title="New chat"):
        conv_id = uuid.uuid4().hex
        conv = {"conversation_id": conv_id, "user_id": user_id, "title": title, "created_at": time.time()}
        self.conversations[conv_id] = conv
        self.messages[conv_id] = []
        return conv

    def list_conversations(self, user_id):
        convs = [c for c in self.conversations.values() if c["user_id"] == user_id]
        return sorted(convs, key=lambda c: c["created_at"], reverse=True)

    def get_conversation(self, conversation_id, user_id):
        c = self.conversations.get(conversation_id)
        return c if c and c["user_id"] == user_id else None

    def add_message(self, conversation_id, role, content, extra=None):
        msg = {"role": role, "content": content, "created_at": time.time(), **(extra or {})}
        self.messages.setdefault(conversation_id, []).append(msg)
        return msg

    def get_messages(self, conversation_id):
        return list(self.messages.get(conversation_id, []))

    def add_ingested_file(self, conversation_id, file_info):
        conv = self.conversations.get(conversation_id)
        if conv:
            if "ingested_files" not in conv:
                conv["ingested_files"] = []
            conv["ingested_files"].append(file_info)
            return file_info
        return None

    def delete_conversation(self, conversation_id):
        if conversation_id in self.conversations:
            del self.conversations[conversation_id]
        if conversation_id in self.messages:
            del self.messages[conversation_id]
        return True

    def remove_ingested_file(self, conversation_id, filename):
        conv = self.conversations.get(conversation_id)
        if conv and "ingested_files" in conv:
            conv["ingested_files"] = [f for f in conv["ingested_files"] if f["filename"] != filename]
            return {"status": "ok"}
        return None


class MongoStore:
    def __init__(self):
        self._mongo_ok = False
        try:
            import pymongo
            self._client = pymongo.MongoClient(settings.MONGO_URL, serverSelectionTimeoutMS=1500)
            self._client.admin.command("ping")
            self._db = self._client[settings.MONGO_DB]
            self._mongo_ok = True
        except Exception as e:
            logger.warning(f"Failed to connect to MongoDB: {e}. Using in-memory fallback.")
        self._fallback = _InMemoryStore()

    @property
    def using_mongo(self):
        return self._mongo_ok

    def upsert_user(self, sub, email, name, picture):
        if not self._mongo_ok:
            return self._fallback.upsert_user(sub, email, name, picture)
        doc = {"user_id": sub, "email": email, "name": name, "picture": picture, "updated_at": time.time()}
        self._db.users.update_one({"user_id": sub}, {"$set": doc}, upsert=True)
        return doc

    def get_user(self, sub):
        if not self._mongo_ok:
            return self._fallback.get_user(sub)
        return self._db.users.find_one({"user_id": sub}, {"_id": 0})

    def create_conversation(self, user_id, title="New chat"):
        if not self._mongo_ok:
            return self._fallback.create_conversation(user_id, title)
        conv = {"conversation_id": uuid.uuid4().hex, "user_id": user_id, "title": title, "created_at": time.time()}
        self._db.conversations.insert_one(dict(conv))
        return conv

    def list_conversations(self, user_id):
        if not self._mongo_ok:
            return self._fallback.list_conversations(user_id)
        return list(self._db.conversations.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1))

    def get_conversation(self, conversation_id, user_id):
        if not self._mongo_ok:
            return self._fallback.get_conversation(conversation_id, user_id)
        return self._db.conversations.find_one({"conversation_id": conversation_id, "user_id": user_id}, {"_id": 0})

    def add_message(self, conversation_id, role, content, extra=None):
        msg = {"conversation_id": conversation_id, "role": role, "content": content, "created_at": time.time(), **(extra or {})}
        if not self._mongo_ok:
            return self._fallback.add_message(conversation_id, role, content, extra)
        self._db.messages.insert_one(dict(msg))
        return msg

    def get_messages(self, conversation_id):
        if not self._mongo_ok:
            return self._fallback.get_messages(conversation_id)
        return list(self._db.messages.find({"conversation_id": conversation_id}, {"_id": 0}).sort("created_at", 1))

    def add_ingested_file(self, conversation_id, user_id, file_info):
        if not self._mongo_ok:
            return self._fallback.add_ingested_file(conversation_id, file_info)
        res = self._db.conversations.update_one(
            {"conversation_id": conversation_id, "user_id": user_id},
            {"$push": {"ingested_files": file_info}}
        )
        if res.matched_count > 0:
            return file_info
        return None

    def delete_conversation(self, conversation_id, user_id):
        if not self._mongo_ok:
            return self._fallback.delete_conversation(conversation_id)
        self._db.conversations.delete_one({"conversation_id": conversation_id, "user_id": user_id})
        self._db.messages.delete_many({"conversation_id": conversation_id})
        return True

    def remove_ingested_file(self, conversation_id, user_id, filename):
        if not self._mongo_ok:
            return self._fallback.remove_ingested_file(conversation_id, filename)
        res = self._db.conversations.update_one(
            {"conversation_id": conversation_id, "user_id": user_id},
            {"$pull": {"ingested_files": {"filename": filename}}}
        )
        if res.matched_count > 0:
            return {"status": "ok"}
        return None


chat_store = MongoStore()


# --- Request/Response ---
class AddMessageReq(BaseModel):
    conversation_id: str
    role: str
    content: str
    metadata: Optional[dict] = None

class UpsertUserReq(BaseModel):
    sub: str
    email: str
    name: str
    picture: str


# --- Routes ---
@app.get("/health")
def health():
    return {"status": "ok", "mongo": chat_store.using_mongo}


@app.post("/users")
def upsert_user(req: UpsertUserReq):
    return chat_store.upsert_user(req.sub, req.email, req.name, req.picture)


@app.get("/users/{user_id}")
def get_user(user_id: str):
    user = chat_store.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@app.get("/conversations")
def list_conversations(user_id: str):
    return chat_store.list_conversations(user_id)


@app.post("/conversations")
def create_conversation(user_id: str, title: str = "New chat"):
    return chat_store.create_conversation(user_id, title)


@app.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user_id: str):
    conv = chat_store.get_conversation(conversation_id, user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@app.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: str):
    return chat_store.get_messages(conversation_id)


@app.post("/messages")
def add_message(req: AddMessageReq):
    return chat_store.add_message(req.conversation_id, req.role, req.content, req.metadata)


class IngestedFileInfo(BaseModel):
    filename: str
    modality: str
    chunks: int


@app.post("/conversations/{conversation_id}/files")
def add_conversation_file(conversation_id: str, user_id: str, file_info: IngestedFileInfo):
    res = chat_store.add_ingested_file(conversation_id, user_id, file_info.dict())
    if not res:
        raise HTTPException(status_code=404, detail="conversation not found")
    return res


@app.get("/conversations/{conversation_id}/files")
def get_conversation_files(conversation_id: str, user_id: str):
    conv = chat_store.get_conversation(conversation_id, user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv.get("ingested_files", [])


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, user_id: str):
    chat_store.delete_conversation(conversation_id, user_id)
    return {"status": "ok"}


@app.delete("/conversations/{conversation_id}/files")
def remove_conversation_file(conversation_id: str, user_id: str, filename: str):
    res = chat_store.remove_ingested_file(conversation_id, user_id, filename)
    if not res:
        raise HTTPException(status_code=404, detail="conversation or file not found")
    return res
