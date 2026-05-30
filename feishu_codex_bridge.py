#!/usr/bin/env python3
"""Feishu to Codex bridge.

This bridge intentionally stays small: it connects Feishu messages to Codex,
returns Codex's own final text, and manages lightweight topic boundaries for a
long-running main bot.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, replace as dataclass_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_DIR = Path.home() / "Library" / "Application Support" / "FeishuCodexBridge"
DEFAULT_WORKDIR = Path.home()
VERSION = "0.2.0"
DEFAULT_TOPIC_IDLE_SECONDS = 2 * 60 * 60
DEFAULT_TASK_PROGRESS_SECONDS = 2 * 60 * 60
DEFAULT_TOPIC_NOTICE_POLL_SECONDS = 60
DEFAULT_GROUP_MEMBER_CACHE_SECONDS = 10 * 60
DEFAULT_ACK_TEXT = "收到，我要开始干活了，稍等我"
MOBILE_REPLY_CONTEXT = (
    "你正在通过手机通信软件回复用户。请使用移动端可读格式：\n"
    "先给结论/摘要/判断；短段落；根据消息类型组织内容；\n"
    "不要输出大段长文；如果内容较长，只回复第一层结论/摘要/判断，用户要求详细答复时再展开。"
)
CARD_REPLY_CONTEXT = (
    "如果需要给用户发送飞书交互卡片，请在最终回复中放一个 ```feishu-card 代码块，内容是飞书卡片 JSON。\n"
    "Bridge 会发送卡片，并从普通文本回复里移除这段 JSON。\n"
    "如果卡片按钮需要继续回调到当前 Codex 会话，请给按钮 value 放业务字段；Bridge 会自动补充回调标记。\n"
    "用户点击后，你会收到形如 [card-click] {...} 的后续消息。"
)
DOC_REPLY_CONTEXT = (
    "如果复杂内容更适合用飞书文档承载，请在最终回复中放 <feishu_doc title=\"标题\">正文</feishu_doc>。\n"
    "Bridge 会创建飞书文档、写入正文，并把文档链接发给用户。"
)
CARD_ACTION_NAME = "codex_card"


@dataclass(frozen=True)
class BridgeConfig:
    app_id: str
    app_secret: str
    workdir: Path = DEFAULT_WORKDIR
    runtime_dir: Path = DEFAULT_RUNTIME_DIR
    codex_bin: str = "codex"
    node_bin: str = ""
    bot_aliases: tuple[str, ...] = ("Codex", "codex", "机器人")
    ignore_older_than_seconds: int = 600
    reply_max_chars: int = 3500
    ack_text: str = DEFAULT_ACK_TEXT
    topic_idle_seconds: int = DEFAULT_TOPIC_IDLE_SECONDS
    topic_notice_enabled: bool = True
    topic_notice_poll_seconds: float = DEFAULT_TOPIC_NOTICE_POLL_SECONDS
    task_progress_seconds: float = DEFAULT_TASK_PROGRESS_SECONDS
    group_auto_reply_enabled: bool = True
    group_auto_reply_max_human_members: int = 1
    group_auto_reply_chat_ids: tuple[str, ...] = ()
    group_member_cache_seconds: float = DEFAULT_GROUP_MEMBER_CACHE_SECONDS
    codex_cards_enabled: bool = True
    cardkit_enabled: bool = False
    docs_enabled: bool = False
    docs_folder_token: str = ""
    docs_domain: str = "https://feishu.cn"
    docs_auto_min_chars: int = 4500
    docs_block_chars: int = 1500

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        return cls(
            app_id=os.getenv("FEISHU_APP_ID", "").strip(),
            app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
            workdir=Path(os.getenv("FEISHU_CODEX_WORKDIR", str(DEFAULT_WORKDIR))).expanduser(),
            runtime_dir=Path(os.getenv("FEISHU_CODEX_RUNTIME_DIR", str(DEFAULT_RUNTIME_DIR))).expanduser(),
            codex_bin=os.getenv("CODEX_BIN", "codex").strip() or "codex",
            node_bin=os.getenv("NODE_BIN", "").strip(),
            bot_aliases=tuple(_csv_env("FEISHU_BOT_ALIASES") or ["Codex", "codex", "机器人"]),
            ignore_older_than_seconds=_int_env("FEISHU_IGNORE_OLD_MESSAGE_SECONDS", 600),
            reply_max_chars=_int_env("FEISHU_REPLY_MAX_CHARS", 3500),
            ack_text=os.getenv("FEISHU_ACK_TEXT", DEFAULT_ACK_TEXT).strip(),
            topic_idle_seconds=_int_env("FEISHU_TOPIC_IDLE_SECONDS", DEFAULT_TOPIC_IDLE_SECONDS),
            topic_notice_enabled=_bool_env("FEISHU_TOPIC_NOTICE_ENABLED", True),
            topic_notice_poll_seconds=_float_env("FEISHU_TOPIC_NOTICE_POLL_SECONDS", DEFAULT_TOPIC_NOTICE_POLL_SECONDS),
            task_progress_seconds=_float_env("FEISHU_TASK_PROGRESS_SECONDS", DEFAULT_TASK_PROGRESS_SECONDS),
            group_auto_reply_enabled=_bool_env("FEISHU_GROUP_AUTO_REPLY_ENABLED", True),
            group_auto_reply_max_human_members=_int_env("FEISHU_GROUP_AUTO_REPLY_MAX_HUMAN_MEMBERS", 1),
            group_auto_reply_chat_ids=tuple(_csv_env("FEISHU_GROUP_AUTO_REPLY_CHAT_IDS")),
            group_member_cache_seconds=_float_env(
                "FEISHU_GROUP_MEMBER_CACHE_SECONDS",
                DEFAULT_GROUP_MEMBER_CACHE_SECONDS,
            ),
            codex_cards_enabled=_bool_env("FEISHU_CODEX_CARDS_ENABLED", True),
            cardkit_enabled=_bool_env("FEISHU_CARDKIT_ENABLED", False),
            docs_enabled=_bool_env("FEISHU_DOCS_ENABLED", False),
            docs_folder_token=os.getenv("FEISHU_DOCS_FOLDER_TOKEN", "").strip(),
            docs_domain=os.getenv("FEISHU_DOCS_DOMAIN", "https://feishu.cn").strip() or "https://feishu.cn",
            docs_auto_min_chars=_int_env("FEISHU_DOCS_AUTO_MIN_CHARS", 4500),
            docs_block_chars=_int_env("FEISHU_DOCS_BLOCK_CHARS", 1500),
        )

    @property
    def db_path(self) -> Path:
        return self.runtime_dir / "state.sqlite"


@dataclass(frozen=True)
class MessageEnvelope:
    message_id: str
    chat_id: str
    chat_type: str
    message_type: str
    text: str
    root_id: str
    parent_id: str
    thread_id: str
    create_time_ms: int | None
    addressed: bool
    sender_id: str = ""
    sender_name: str = ""
    group_auto_reply: bool = False


@dataclass(frozen=True)
class RouteDecision:
    session_key: str
    should_handle: bool
    reason: str
    starts_new_container: bool = False


@dataclass(frozen=True)
class TopicNotice:
    base_session_key: str
    previous_session_key: str
    active_session_key: str
    idle_seconds: int


@dataclass(frozen=True)
class TopicResolution:
    route: RouteDecision
    notice: TopicNotice | None = None


@dataclass(frozen=True)
class PendingTopicNotice:
    chat_id: str
    notice: TopicNotice


@dataclass(frozen=True)
class FeishuDocRequest:
    title: str
    content: str


@dataclass(frozen=True)
class FeishuDocumentResult:
    title: str
    document_id: str
    url: str


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def connect(self) -> Any:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                create table if not exists sessions (
                    session_key text primary key,
                    codex_thread_id text,
                    title text,
                    source text not null,
                    created_at text not null,
                    updated_at text not null
                );
                create table if not exists messages (
                    id integer primary key autoincrement,
                    session_key text not null,
                    feishu_message_id text not null unique,
                    role text not null,
                    text text not null,
                    reply_text text,
                    create_time_ms integer,
                    created_at text not null
                );
                create table if not exists topic_states (
                    base_session_key text primary key,
                    active_session_key text not null,
                    previous_session_key text,
                    topic_seq integer not null default 1,
                    last_message_ms integer,
                    active_task_count integer not null default 0,
                    last_task_completed_ms integer,
                    idle_notice_sent_ms integer,
                    idle_notice_session_key text,
                    topic_started_at text not null,
                    updated_at text not null
                );
                """
            )
            self._ensure_column(con, "topic_states", "active_task_count", "integer not null default 0")
            self._ensure_column(con, "topic_states", "last_task_completed_ms", "integer")
            self._ensure_column(con, "topic_states", "idle_notice_sent_ms", "integer")
            self._ensure_column(con, "topic_states", "idle_notice_session_key", "text")

    def has_seen(self, message_id: str) -> bool:
        if not message_id:
            return False
        with self.connect() as con:
            row = con.execute("select 1 from messages where feishu_message_id = ?", (message_id,)).fetchone()
            return row is not None

    def get_thread_id(self, session_key: str) -> str | None:
        with self.connect() as con:
            row = con.execute(
                "select codex_thread_id from sessions where session_key = ?",
                (session_key,),
            ).fetchone()
            return str(row["codex_thread_id"]) if row and row["codex_thread_id"] else None

    def upsert_session(self, session_key: str, source: str, title: str = "") -> None:
        now = now_iso()
        with self.connect() as con:
            con.execute(
                """
                insert into sessions(session_key, codex_thread_id, title, source, created_at, updated_at)
                values(?, null, ?, ?, ?, ?)
                on conflict(session_key) do update set updated_at = excluded.updated_at
                """,
                (session_key, title, source, now, now),
            )

    def set_thread_id(self, session_key: str, thread_id: str) -> None:
        with self.connect() as con:
            con.execute(
                "update sessions set codex_thread_id = ?, updated_at = ? where session_key = ?",
                (thread_id, now_iso(), session_key),
            )

    def reset_session(self, session_key: str) -> None:
        with self.connect() as con:
            con.execute(
                "update sessions set codex_thread_id = null, updated_at = ? where session_key = ?",
                (now_iso(), session_key),
            )

    def record_user_message(self, session_key: str, envelope: MessageEnvelope) -> None:
        with self.connect() as con:
            con.execute(
                """
                insert or ignore into messages(
                    session_key, feishu_message_id, role, text, create_time_ms, created_at
                )
                values(?, ?, 'user', ?, ?, ?)
                """,
                (session_key, envelope.message_id, envelope.text, envelope.create_time_ms, now_iso()),
            )

    def record_reply(self, message_id: str, reply_text: str) -> None:
        with self.connect() as con:
            con.execute(
                "update messages set reply_text = ? where feishu_message_id = ?",
                (reply_text, message_id),
            )

    def resolve_topic(self, route: RouteDecision, envelope: MessageEnvelope, idle_seconds: int) -> TopicResolution:
        if route.reason != "direct-chat" or idle_seconds <= 0:
            return TopicResolution(route)
        now_ms = envelope.create_time_ms or int(time.time() * 1000)
        now = now_iso()
        with self.connect() as con:
            row = con.execute(
                "select * from topic_states where base_session_key = ?",
                (route.session_key,),
            ).fetchone()
            if row is None:
                con.execute(
                    """
                    insert into topic_states(
                        base_session_key, active_session_key, previous_session_key,
                        topic_seq, last_message_ms, topic_started_at, updated_at
                    )
                    values(?, ?, null, 1, ?, ?, ?)
                    """,
                    (route.session_key, route.session_key, now_ms, now, now),
                )
                return TopicResolution(route)

            active_key = str(row["active_session_key"] or route.session_key)
            last_message_ms = int(row["last_message_ms"] or 0)
            active_task_count = int(row["active_task_count"] or 0)
            if active_task_count > 0:
                con.execute(
                    """
                    update topic_states
                    set last_message_ms = ?,
                        idle_notice_sent_ms = null,
                        idle_notice_session_key = null,
                        updated_at = ?
                    where base_session_key = ?
                    """,
                    (now_ms, now, route.session_key),
                )
                return TopicResolution(replace_route(route, active_key, "active-task"))
            con.execute(
                """
                update topic_states
                set last_message_ms = ?,
                    idle_notice_sent_ms = null,
                    idle_notice_session_key = null,
                    updated_at = ?
                where base_session_key = ?
                """,
                (now_ms, now, route.session_key),
            )
            return TopicResolution(replace_route(route, active_key, route.reason))

    def claim_due_topic_notices(
        self,
        idle_seconds: int,
        now_ms: int | None = None,
        limit: int = 20,
    ) -> list[PendingTopicNotice]:
        if idle_seconds <= 0:
            return []
        now_ms = now_ms or int(time.time() * 1000)
        cutoff_ms = now_ms - idle_seconds * 1000
        now = now_iso()
        notices: list[PendingTopicNotice] = []
        with self.connect() as con:
            rows = con.execute(
                """
                select * from topic_states
                where active_task_count = 0
                  and last_message_ms is not null
                  and last_message_ms <= ?
                  and (
                      idle_notice_sent_ms is null
                      or idle_notice_session_key is null
                      or idle_notice_session_key != active_session_key
                  )
                order by last_message_ms asc
                limit ?
                """,
                (cutoff_ms, limit),
            ).fetchall()
            for row in rows:
                base_key = str(row["base_session_key"] or "")
                chat_id = p2p_chat_id_from_session_key(base_key)
                if not chat_id:
                    continue
                active_key = str(row["active_session_key"] or base_key)
                topic_seq = int(row["topic_seq"] or 1) + 1
                new_key = f"{base_key}:topic:{topic_seq}"
                cursor = con.execute(
                    """
                    update topic_states
                    set active_session_key = ?,
                        previous_session_key = ?,
                        topic_seq = ?,
                        idle_notice_sent_ms = ?,
                        idle_notice_session_key = ?,
                        topic_started_at = ?,
                        updated_at = ?
                    where base_session_key = ?
                      and active_session_key = ?
                      and active_task_count = 0
                    """,
                    (new_key, active_key, topic_seq, now_ms, new_key, now, now, base_key, active_key),
                )
                if cursor.rowcount <= 0:
                    continue
                self._upsert_session_on_connection(con, new_key, "idle-notice-new-topic", "空闲后自动开启新话题")
                notices.append(
                    PendingTopicNotice(
                        chat_id=chat_id,
                        notice=TopicNotice(
                            base_session_key=base_key,
                            previous_session_key=active_key,
                            active_session_key=new_key,
                            idle_seconds=idle_seconds,
                        ),
                    )
                )
        return notices

    def restore_previous_topic(
        self,
        base_session_key: str,
        expected_active_session_key: str | None = None,
        now_ms: int | None = None,
    ) -> str | None:
        with self.connect() as con:
            row = con.execute(
                "select active_session_key, previous_session_key from topic_states where base_session_key = ?",
                (base_session_key,),
            ).fetchone()
            if row is None:
                return None
            active_key = str(row["active_session_key"] or "")
            previous_key = str(row["previous_session_key"] or "")
            if expected_active_session_key and active_key != expected_active_session_key:
                return None
            if not previous_key:
                return active_key or None
            con.execute(
                """
                update topic_states
                set active_session_key = ?,
                    previous_session_key = ?,
                    last_message_ms = ?,
                    idle_notice_sent_ms = null,
                    idle_notice_session_key = null,
                    updated_at = ?
                where base_session_key = ?
                """,
                (previous_key, active_key or None, now_ms or int(time.time() * 1000), now_iso(), base_session_key),
            )
            return previous_key

    def begin_task(self, session_key: str, started_ms: int | None = None) -> None:
        now_ms = started_ms or int(time.time() * 1000)
        now = now_iso()
        with self.connect() as con:
            con.execute(
                """
                update topic_states
                set active_task_count = active_task_count + 1,
                    last_message_ms = ?,
                    idle_notice_sent_ms = null,
                    idle_notice_session_key = null,
                    updated_at = ?
                where active_session_key = ? or base_session_key = ?
                """,
                (now_ms, now, session_key, session_key),
            )

    def finish_task(self, session_key: str, completed_ms: int | None = None) -> None:
        now_ms = completed_ms or int(time.time() * 1000)
        now = now_iso()
        with self.connect() as con:
            con.execute(
                """
                update topic_states
                set active_task_count = max(active_task_count - 1, 0),
                    last_task_completed_ms = ?,
                    last_message_ms = ?,
                    idle_notice_sent_ms = null,
                    idle_notice_session_key = null,
                    updated_at = ?
                where active_session_key = ? or base_session_key = ?
                """,
                (now_ms, now_ms, now, session_key, session_key),
            )

    def keep_current_topic(self, base_session_key: str) -> str | None:
        now_ms = int(time.time() * 1000)
        with self.connect() as con:
            row = con.execute(
                "select active_session_key from topic_states where base_session_key = ?",
                (base_session_key,),
            ).fetchone()
            if not row or not row["active_session_key"]:
                return None
            active_key = str(row["active_session_key"])
            con.execute(
                """
                update topic_states
                set previous_session_key = null,
                    last_message_ms = ?,
                    idle_notice_sent_ms = null,
                    idle_notice_session_key = null,
                    updated_at = ?
                where base_session_key = ?
                """,
                (now_ms, now_iso(), base_session_key),
            )
            return active_key

    def _ensure_column(self, con: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        rows = con.execute(f"pragma table_info({table})").fetchall()
        if column in {str(row["name"]) for row in rows}:
            return
        con.execute(f"alter table {table} add column {column} {definition}")

    def _upsert_session_on_connection(self, con: sqlite3.Connection, session_key: str, source: str, title: str = "") -> None:
        now = now_iso()
        con.execute(
            """
            insert into sessions(session_key, codex_thread_id, title, source, created_at, updated_at)
            values(?, null, ?, ?, ?, ?)
            on conflict(session_key) do update set updated_at = excluded.updated_at
            """,
            (session_key, title, source, now, now),
        )


class CodexRunner:
    def __init__(self, config: BridgeConfig) -> None:
        self.config = config

    def run(self, prompt: str, thread_id: str | None) -> tuple[str | None, str]:
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as output_file:
            output_path = Path(output_file.name)
        try:
            codex_command = self._codex_command()
            if thread_id:
                command = [
                    *codex_command,
                    "exec",
                    "resume",
                    "--json",
                    "--skip-git-repo-check",
                    "-o",
                    str(output_path),
                    thread_id,
                    "-",
                ]
            else:
                command = [
                    *codex_command,
                    "exec",
                    "--json",
                    "--skip-git-repo-check",
                    "-C",
                    str(self.config.workdir),
                    "-o",
                    str(output_path),
                    "-",
                ]
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=str(self.config.workdir),
                check=False,
            )
            new_thread_id = thread_id or parse_thread_id(completed.stdout)
            reply = output_path.read_text(encoding="utf-8").strip()
            if not reply:
                reply = parse_last_agent_message(completed.stdout) or completed.stderr.strip()
            if completed.returncode != 0:
                detail = reply or f"退出码 {completed.returncode}。"
                reply = f"Codex 执行异常终止，退出码 {completed.returncode}。\n\n{detail}".strip()
            return new_thread_id, reply.strip()
        finally:
            output_path.unlink(missing_ok=True)

    def _codex_command(self) -> list[str]:
        if self.config.node_bin:
            codex_path = Path(self.config.codex_bin).expanduser()
            resolved_path = codex_path.resolve() if codex_path.exists() else codex_path
            if str(codex_path).endswith(".js") or str(resolved_path).endswith(".js"):
                return [self.config.node_bin, str(resolved_path)]
        return [self.config.codex_bin]


class FeishuOpenAPIClient:
    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self._tenant_access_token = ""
        self._token_expires_at = 0.0

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        encoded_query = urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v is not None})
        url = f"https://open.feishu.cn/open-apis{path}"
        if encoded_query:
            url = f"{url}?{encoded_query}"
        headers = {
            "Authorization": f"Bearer {self.tenant_access_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = self._request_json(method, url, body or {}, headers)
        code = payload.get("code", 0)
        if code not in {0, "0"}:
            raise RuntimeError(f"Feishu OpenAPI failed: code={code} msg={payload.get('msg', '')}")
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def tenant_access_token(self) -> str:
        if self._tenant_access_token and time.time() < self._token_expires_at - 60:
            return self._tenant_access_token
        payload = self._request_json(
            "POST",
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": self.config.app_id, "app_secret": self.config.app_secret},
            {"Content-Type": "application/json; charset=utf-8"},
        )
        code = payload.get("code", 0)
        if code not in {0, "0"}:
            raise RuntimeError(f"Feishu token request failed: code={code} msg={payload.get('msg', '')}")
        token = str(payload.get("tenant_access_token", "") or "")
        if not token:
            raise RuntimeError("Feishu token request returned no tenant_access_token.")
        self._tenant_access_token = token
        self._token_expires_at = time.time() + int(payload.get("expire", 7200) or 7200)
        return token

    def create_card_id(self, card: dict[str, Any]) -> str:
        data = self.request(
            "POST",
            "/cardkit/v1/cards",
            {"type": "card_json", "data": json.dumps(card, ensure_ascii=False)},
        )
        card_id = str(data.get("card_id") or data.get("card", {}).get("card_id") or "")
        if not card_id:
            raise RuntimeError(f"cardkit create returned no card_id: {json.dumps(data, ensure_ascii=False)[:200]}")
        return card_id

    def chat_human_member_count(self, chat_id: str) -> int:
        count = 0
        page_token = ""
        while True:
            query: dict[str, Any] = {"member_id_type": "open_id", "page_size": 100}
            if page_token:
                query["page_token"] = page_token
            data = self.request(
                "GET",
                f"/im/v1/chats/{urllib.parse.quote(chat_id, safe='')}/members",
                query=query,
            )
            items = data.get("items") if isinstance(data.get("items"), list) else []
            count += sum(1 for item in items if isinstance(item, dict) and not is_bot_member(item))
            if not data.get("has_more"):
                return count
            page_token = str(data.get("page_token") or "")
            if not page_token:
                return count

    def _request_json(self, method: str, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        method = method.upper()
        data = None if method == "GET" and not body else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Feishu HTTP {exc.code}: {detail[:500]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Feishu HTTP request failed: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Feishu returned non-JSON response: {raw[:500]}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Feishu returned unexpected JSON response.")
        return payload


def is_bot_member(item: dict[str, Any]) -> bool:
    marker = (
        item.get("member_type")
        or item.get("type")
        or item.get("user_type")
        or item.get("memberType")
        or item.get("userType")
        or ""
    )
    return str(marker).strip().casefold() in {"bot", "app", "robot"}


class FeishuDocClient:
    def __init__(self, config: BridgeConfig, api: FeishuOpenAPIClient) -> None:
        self.config = config
        self.api = api

    def create_document(self, request: FeishuDocRequest) -> FeishuDocumentResult:
        query = {"folder_token": self.config.docs_folder_token} if self.config.docs_folder_token else None
        data = self.api.request("POST", "/docx/v1/documents", {"title": request.title}, query)
        document = data.get("document") if isinstance(data.get("document"), dict) else data
        document_id = str(
            document.get("document_id")
            or document.get("document_token")
            or document.get("token")
            or data.get("document_id")
            or ""
        )
        if not document_id:
            raise RuntimeError(f"docx create returned no document_id: {json.dumps(data, ensure_ascii=False)[:200]}")
        if request.content.strip():
            self._append_text(document_id, request.content)
        return FeishuDocumentResult(
            title=request.title,
            document_id=document_id,
            url=f"{self.config.docs_domain.rstrip('/')}/docx/{document_id}",
        )

    def _append_text(self, document_id: str, content: str) -> None:
        chunks = chunk_text(content.strip(), max(200, self.config.docs_block_chars))
        if not chunks:
            return
        for batch in batched(chunks, 20):
            children = [
                {
                    "block_type": 2,
                    "text": {
                        "elements": [{"text_run": {"content": chunk, "text_element_style": {}}}],
                        "style": {},
                    },
                }
                for chunk in batch
            ]
            self.api.request(
                "POST",
                f"/docx/v1/documents/{urllib.parse.quote(document_id)}/blocks/{urllib.parse.quote(document_id)}/children",
                {"children": children, "index": -1},
                {"document_revision_id": -1},
            )


def route_message(envelope: MessageEnvelope) -> RouteDecision:
    if envelope.chat_type == "p2p":
        return RouteDecision(
            session_key=f"feishu:p2p:{envelope.chat_id}",
            should_handle=True,
            reason="direct-chat",
        )

    container_id = envelope.thread_id or envelope.root_id or envelope.parent_id
    if container_id:
        return RouteDecision(
            session_key=f"feishu:{envelope.chat_id}:thread:{container_id}",
            should_handle=True,
            reason="thread-or-reply",
        )

    if envelope.group_auto_reply:
        return RouteDecision(
            session_key=f"feishu:{envelope.chat_id}:direct",
            should_handle=True,
            reason="small-group-direct",
        )

    if envelope.addressed:
        return RouteDecision(
            session_key=f"feishu:{envelope.chat_id}:thread:{envelope.message_id}",
            should_handle=True,
            reason="group-mention-starts-task",
            starts_new_container=True,
        )

    return RouteDecision(
        session_key=f"feishu:{envelope.chat_id}:ignored",
        should_handle=False,
        reason="group-message-not-addressed",
    )


def p2p_chat_id_from_session_key(session_key: str) -> str:
    prefix = "feishu:p2p:"
    if not session_key.startswith(prefix):
        return ""
    chat_id = session_key[len(prefix) :]
    if ":topic:" in chat_id:
        return chat_id.split(":topic:", 1)[0]
    return chat_id


def replace_route(route: RouteDecision, session_key: str, reason: str) -> RouteDecision:
    return RouteDecision(
        session_key=session_key,
        should_handle=route.should_handle,
        reason=reason,
        starts_new_container=route.starts_new_container,
    )


def build_prompt(envelope: MessageEnvelope, route: RouteDecision, config: BridgeConfig | None = None) -> str:
    capabilities = [MOBILE_REPLY_CONTEXT]
    if config is None or config.codex_cards_enabled:
        capabilities.append(CARD_REPLY_CONTEXT)
    if config and config.docs_enabled:
        capabilities.append(DOC_REPLY_CONTEXT)
    bridge_context = {
        "chat_id": envelope.chat_id,
        "chat_type": envelope.chat_type,
        "route": route.reason,
        "session_key": route.session_key,
        "sender_id": envelope.sender_id,
        "sender_name": envelope.sender_name,
    }
    return (
        "这是一条从飞书转发到 Codex 的用户消息。\n"
        "按当前工作区规则处理；不要把飞书入口绑定到任何单一技能或工具。\n"
        "如果用户明确要求调研、读取链接、编辑文件或运行工具，你可以按 Codex 正常能力选择合适方法。\n\n"
        + "\n\n".join(capabilities)
        + "\n\n<bridge_context>\n"
        + json.dumps(bridge_context, ensure_ascii=False)
        + "\n</bridge_context>\n\n"
        f"用户消息：\n{envelope.text.strip()}"
    )


def build_card_action_prompt(
    envelope: MessageEnvelope,
    route: RouteDecision,
    payload: dict[str, Any],
    config: BridgeConfig | None = None,
) -> str:
    bridge_context = {
        "chat_id": envelope.chat_id,
        "chat_type": envelope.chat_type,
        "route": route.reason,
        "session_key": route.session_key,
        "sender_id": envelope.sender_id,
        "sender_name": envelope.sender_name,
    }
    capabilities = [MOBILE_REPLY_CONTEXT]
    if config is None or config.codex_cards_enabled:
        capabilities.append(CARD_REPLY_CONTEXT)
    if config and config.docs_enabled:
        capabilities.append(DOC_REPLY_CONTEXT)
    return (
        "用户点击了你上一轮发出的飞书交互卡片。请把它当作当前会话的后续输入处理。\n\n"
        + "\n\n".join(capabilities)
        + "\n\n<bridge_context>\n"
        + json.dumps(bridge_context, ensure_ascii=False)
        + "\n</bridge_context>\n\n"
        "[card-click] "
        + json.dumps(payload, ensure_ascii=False)
    )


def handle_command(store: StateStore, route: RouteDecision, text: str, config: BridgeConfig | None = None) -> str | None:
    normalized = text.strip()
    if normalized in {"/new", "新会话", "重新开始"}:
        store.upsert_session(route.session_key, route.reason)
        store.reset_session(route.session_key)
        return "已重新开始这段 Codex 对话。"
    if normalized in {"/clear", "清空上下文"}:
        store.upsert_session(route.session_key, route.reason)
        store.reset_session(route.session_key)
        return "已清空这段飞书容器绑定的 Codex 对话。"
    if normalized in {"/status", "当前会话"}:
        thread_id = store.get_thread_id(route.session_key)
        return f"当前飞书容器：{route.session_key}\nCodex 会话：{thread_id or '尚未创建'}"
    if normalized in {"/docs", "/doc", "文档能力", "飞书文档"}:
        if not config or not config.docs_enabled:
            return (
                "飞书文档能力未启用。\n"
                "开启方式：给机器人申请新版文档创建/编辑权限，配置 FEISHU_DOCS_ENABLED=1；"
                "如需把文档放入指定文件夹，再配置 FEISHU_DOCS_FOLDER_TOKEN。"
            )
        folder = config.docs_folder_token or "未指定，会创建在应用默认位置"
        return (
            "飞书文档能力已启用。\n"
            f"文档域名：{config.docs_domain}\n"
            f"目标文件夹：{folder}\n"
            "Codex 可以用 <feishu_doc title=\"标题\">正文</feishu_doc> 让 Bridge 创建文档并发回链接。"
        )
    return None


def handle_topic_command(store: StateStore, base_route: RouteDecision, text: str) -> str | None:
    normalized = text.strip()
    if base_route.reason != "direct-chat":
        return None
    if normalized in {"继续上个话题", "继续刚才那个", "继续刚才的话题", "回到上个话题"}:
        restored = store.restore_previous_topic(base_route.session_key)
        if restored:
            return "已切回上个话题。"
        return "当前没有可恢复的上个话题。"
    return None


def extract_envelope(event: Any, bot_aliases: tuple[str, ...]) -> MessageEnvelope:
    message = getattr(getattr(event, "event", None), "message", None)
    if not message:
        return MessageEnvelope("", "", "", "unknown", "", "", "", "", None, False)
    text = extract_message_text(event)
    sender_id, sender_name = extract_sender(event)
    return MessageEnvelope(
        message_id=str(getattr(message, "message_id", "") or ""),
        chat_id=str(getattr(message, "chat_id", "") or ""),
        chat_type=str(getattr(message, "chat_type", "") or ""),
        message_type=str(getattr(message, "message_type", "") or "unknown"),
        text=text,
        root_id=str(getattr(message, "root_id", "") or ""),
        parent_id=str(getattr(message, "parent_id", "") or ""),
        thread_id=str(getattr(message, "thread_id", "") or ""),
        create_time_ms=extract_message_create_time_ms(event),
        addressed=is_addressed_to_bot(message, text, bot_aliases),
        sender_id=sender_id,
        sender_name=sender_name,
    )


def extract_message_text(event: Any) -> str:
    message = getattr(getattr(event, "event", None), "message", None)
    if not message:
        return ""
    content = getattr(message, "content", "") or ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return str(content)
    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        return normalize_mentions(payload["text"])
    return normalize_mentions(flatten_payload_text(payload))


def extract_message_create_time_ms(event: Any) -> int | None:
    message = getattr(getattr(event, "event", None), "message", None)
    value = getattr(message, "create_time", "") if message else ""
    try:
        timestamp = int(str(value))
    except (TypeError, ValueError):
        return None
    if timestamp and timestamp < 10_000_000_000:
        return timestamp * 1000
    return timestamp or None


def extract_sender(event: Any) -> tuple[str, str]:
    sender = getattr(getattr(event, "event", None), "sender", None)
    sender_id = getattr(sender, "sender_id", None)
    open_id = getattr(sender_id, "open_id", "") if sender_id else ""
    user_id = getattr(sender_id, "user_id", "") if sender_id else ""
    union_id = getattr(sender_id, "union_id", "") if sender_id else ""
    name = (
        getattr(sender, "sender_name", "")
        or getattr(sender, "name", "")
        or getattr(sender, "tenant_key", "")
        or ""
    )
    return str(open_id or user_id or union_id or ""), str(name or "")


def is_addressed_to_bot(message: Any, text: str, bot_aliases: tuple[str, ...]) -> bool:
    mentions = getattr(message, "mentions", None)
    if mentions:
        return True
    stripped = text.strip()
    if stripped.startswith(("/", "／")):
        return True
    lowered = stripped.casefold()
    return any(alias and alias.casefold() in lowered for alias in bot_aliases)


def flatten_payload_text(payload: Any) -> str:
    fragments: list[str] = []
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        for item in payload:
            fragments.append(flatten_payload_text(item))
        return " ".join(part for part in fragments if part)
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in {"text", "title", "name", "file_name", "filename", "fileName"} and isinstance(value, str):
                fragments.append(value)
            elif isinstance(value, (dict, list)):
                fragments.append(flatten_payload_text(value))
    return " ".join(part for part in fragments if part)


def normalize_mentions(text: str) -> str:
    return re.sub(r"@\S+\s+", "", text.replace("\u200b", " ")).strip()


def parse_thread_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "thread.started" and payload.get("thread_id"):
            return str(payload["thread_id"])
    return None


def parse_last_agent_message(stdout: str) -> str | None:
    last: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = payload.get("item") or {}
        if payload.get("type") == "item.completed" and item.get("type") == "agent_message":
            last = str(item.get("text", "")).strip()
    return last


def truncate_reply(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 80].rstrip() + "\n\n...后续内容较长，已在本机 Codex 会话中保留。"


def extract_feishu_cards(text: str) -> tuple[str, list[dict[str, Any]], list[str]]:
    cards: list[dict[str, Any]] = []
    errors: list[str] = []

    def parse_block(raw: str) -> str:
        try:
            card = json.loads(raw.strip())
        except json.JSONDecodeError as exc:
            errors.append(f"飞书卡片 JSON 解析失败：{exc.msg}")
            return ""
        if not isinstance(card, dict):
            errors.append("飞书卡片 JSON 必须是对象。")
            return ""
        cards.append(card)
        return ""

    patterns = [
        re.compile(r"```(?:feishu-card|feishu_card|lark-card|lark_card)\s*\n(.*?)\n```", re.DOTALL),
        re.compile(r"<feishu_card>\s*(.*?)\s*</feishu_card>", re.DOTALL),
    ]
    cleaned = text
    for pattern in patterns:
        cleaned = pattern.sub(lambda match: parse_block(match.group(1)), cleaned)
    return cleaned.strip(), cards, errors


def extract_feishu_docs(text: str) -> tuple[str, list[FeishuDocRequest], list[str]]:
    docs: list[FeishuDocRequest] = []
    errors: list[str] = []

    def add_doc(title: str, content: str) -> None:
        clean_title = compact_title(title, "Codex 输出")
        clean_content = content.strip()
        if not clean_content:
            errors.append(f"飞书文档「{clean_title}」正文为空，未创建。")
            return
        docs.append(FeishuDocRequest(clean_title, clean_content))

    def parse_json_doc(raw: str) -> str:
        try:
            payload = json.loads(raw.strip())
        except json.JSONDecodeError as exc:
            errors.append(f"飞书文档 JSON 解析失败：{exc.msg}")
            return ""
        if not isinstance(payload, dict):
            errors.append("飞书文档 JSON 必须是对象。")
            return ""
        content = payload.get("content") or payload.get("body") or payload.get("text") or ""
        add_doc(str(payload.get("title") or "Codex 输出"), str(content))
        return ""

    fenced = re.compile(r"```(?:feishu-doc|feishu_doc|lark-doc|lark_doc)\s*\n(.*?)\n```", re.DOTALL)
    tagged = re.compile(r"<feishu_doc(?:\s+title=\"([^\"]*)\")?\s*>(.*?)</feishu_doc>", re.DOTALL)
    cleaned = fenced.sub(lambda match: parse_json_doc(match.group(1)), text)
    cleaned = tagged.sub(lambda match: add_doc(match.group(1) or "Codex 输出", match.group(2)) or "", cleaned)
    return cleaned.strip(), docs, errors


def stamp_codex_card_callbacks(card: dict[str, Any], route: RouteDecision, origin_message_id: str) -> dict[str, Any]:
    def stamp_value(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        existing_action = value.get("bridge_action")
        if existing_action and existing_action != CARD_ACTION_NAME:
            return value
        stamped = dict(value)
        stamped["bridge_action"] = CARD_ACTION_NAME
        stamped["session_key"] = route.session_key
        stamped["origin_message_id"] = origin_message_id
        return stamped

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        result = {key: walk(value) for key, value in node.items()}
        if "value" in result and isinstance(result.get("value"), dict):
            result["value"] = stamp_value(result["value"])
        behaviors = result.get("behaviors")
        if isinstance(behaviors, list):
            for behavior in behaviors:
                if isinstance(behavior, dict) and behavior.get("type") == "callback":
                    behavior["value"] = stamp_value(behavior.get("value") or {})
        return result

    return walk(card)


def extract_card_action_value(data: Any) -> dict[str, Any]:
    action = getattr(getattr(getattr(data, "event", None), "action", None), "value", None)
    if isinstance(action, str):
        try:
            action = json.loads(action)
        except json.JSONDecodeError:
            action = {}
    return action if isinstance(action, dict) else {}


def extract_card_action_form_value(data: Any) -> dict[str, Any] | None:
    action = getattr(getattr(data, "event", None), "action", None)
    form_value = getattr(action, "form_value", None)
    return form_value if isinstance(form_value, dict) else None


def extract_card_action_message_id(data: Any) -> str:
    event = getattr(data, "event", None)
    context = getattr(event, "context", None)
    for obj in (event, context):
        for name in ("message_id", "open_message_id", "openMessageId"):
            value = getattr(obj, name, "") if obj else ""
            if value:
                return str(value)
    return ""


def extract_card_action_chat(data: Any) -> tuple[str, str, str, str]:
    event = getattr(data, "event", None)
    context = getattr(event, "context", None)
    operator = getattr(event, "operator", None)
    operator_id = getattr(operator, "open_id", "") or getattr(operator, "openId", "") or getattr(operator, "user_id", "")
    operator_name = getattr(operator, "name", "") or getattr(operator, "en_name", "") or ""
    chat_id = (
        getattr(event, "chat_id", "")
        or getattr(event, "chatId", "")
        or getattr(context, "open_chat_id", "")
        or getattr(context, "openChatId", "")
        or ""
    )
    return str(chat_id or ""), "p2p", str(operator_id or ""), str(operator_name or "")


def card_action_payload(action: dict[str, Any], form_value: dict[str, Any] | None) -> dict[str, Any]:
    payload = {key: value for key, value in action.items() if key not in {"bridge_action", "session_key", "origin_message_id"}}
    if form_value:
        payload["form_value"] = form_value
    if set(payload.keys()) == {"payload"} and isinstance(payload["payload"], dict):
        payload = dict(payload["payload"])
        if form_value:
            payload["form_value"] = form_value
    return payload


def chunk_text(text: str, size: int) -> list[str]:
    if size <= 0:
        return [text]
    return [text[index : index + size] for index in range(0, len(text), size)]


def batched(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def compact_title(text: str, default: str = "Codex 输出", limit: int = 60) -> str:
    title = re.sub(r"\s+", " ", text).strip(" #`*_")
    if not title:
        title = default
    return title[:limit].rstrip() or default


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}小时{minutes}分钟"
    if minutes:
        return f"{minutes}分钟{secs}秒"
    return f"{secs}秒"


def build_topic_notice_card(notice: TopicNotice) -> dict[str, Any]:
    hours = max(1, round(notice.idle_seconds / 3600))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "已进入新话题"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"当前没有正在执行的任务，已空闲超过 {hours} 小时。后续消息默认进入新话题。\n"
                        "如需延续，请点击「继续上个话题」。"
                    ),
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "type": "primary",
                        "text": {"tag": "plain_text", "content": "继续上个话题"},
                        "value": {
                            "bridge_action": "topic_boundary",
                            "choice": "continue_previous",
                            "base_session_key": notice.base_session_key,
                            "active_session_key": notice.active_session_key,
                        },
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "保持新话题"},
                        "value": {
                            "bridge_action": "topic_boundary",
                            "choice": "keep_current",
                            "base_session_key": notice.base_session_key,
                        },
                    },
                ],
            },
        ],
    }


def topic_action_response(content: str, type_: str = "success") -> Any:
    try:
        from lark_oapi.event.callback.model.p2_card_action_trigger import CallBackToast, P2CardActionTriggerResponse
    except Exception:
        return None
    response = P2CardActionTriggerResponse()
    response.toast = CallBackToast()
    response.toast.type = type_
    response.toast.content = content
    return response


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


class FeishuCodexBridge:
    def __init__(self, config: BridgeConfig, api_client: Any, reply_request: Any, reply_body: Any) -> None:
        self.config = config
        self.store = StateStore(config.db_path)
        self.runner = CodexRunner(config)
        self.api_client = api_client
        self.reply_request = reply_request
        self.reply_body = reply_body
        self.open_api = FeishuOpenAPIClient(config)
        self.doc_client = FeishuDocClient(config, self.open_api) if config.docs_enabled else None
        self._idle_notice_thread: threading.Thread | None = None
        self._group_member_count_cache: dict[str, tuple[float, int | None]] = {}

    def start_idle_topic_notifier(self) -> threading.Thread | None:
        if not self.config.topic_notice_enabled or self.config.topic_idle_seconds <= 0:
            return None
        if self._idle_notice_thread and self._idle_notice_thread.is_alive():
            return self._idle_notice_thread
        interval = max(1.0, self.config.topic_notice_poll_seconds)

        def run() -> None:
            while True:
                try:
                    self._send_due_idle_topic_notices()
                except Exception as exc:
                    print(f"Idle topic notice scan failed: {exc}", flush=True)
                time.sleep(interval)

        self._idle_notice_thread = threading.Thread(target=run, daemon=True)
        self._idle_notice_thread.start()
        return self._idle_notice_thread

    def _send_due_idle_topic_notices(self, now_ms: int | None = None) -> int:
        sent = 0
        pending = self.store.claim_due_topic_notices(self.config.topic_idle_seconds, now_ms=now_ms)
        for item in pending:
            if self._send_interactive_message(item.chat_id, build_topic_notice_card(item.notice)):
                sent += 1
            else:
                print(f"Idle topic notice send failed for {item.notice.base_session_key}", flush=True)
        return sent

    def _apply_small_group_auto_reply(self, envelope: MessageEnvelope) -> MessageEnvelope:
        if self._should_auto_reply_small_group(envelope):
            return dataclass_replace(envelope, group_auto_reply=True)
        return envelope

    def _should_auto_reply_small_group(self, envelope: MessageEnvelope) -> bool:
        if envelope.chat_type != "group":
            return False
        if envelope.addressed or envelope.thread_id or envelope.root_id or envelope.parent_id:
            return False
        if envelope.chat_id in self.config.group_auto_reply_chat_ids:
            return True
        if not self.config.group_auto_reply_enabled or self.config.group_auto_reply_max_human_members <= 0:
            return False
        human_count = self._group_human_member_count(envelope.chat_id)
        return human_count is not None and 0 < human_count <= self.config.group_auto_reply_max_human_members

    def _group_human_member_count(self, chat_id: str) -> int | None:
        now = time.monotonic()
        ttl = self.config.group_member_cache_seconds
        cached = self._group_member_count_cache.get(chat_id)
        if cached and ttl > 0 and now - cached[0] < ttl:
            return cached[1]
        try:
            count = self.open_api.chat_human_member_count(chat_id)
        except Exception as exc:
            print(f"Group member lookup failed for {chat_id}: {exc}", flush=True)
            count = None
        if ttl > 0:
            self._group_member_count_cache[chat_id] = (now, count)
        return count

    def handle_message(self, data: Any) -> None:
        envelope = extract_envelope(data, self.config.bot_aliases)
        if not envelope.message_id:
            return
        if self._is_stale(envelope):
            print(f"Skip stale message {envelope.message_id}")
            return
        if self.store.has_seen(envelope.message_id):
            print(f"Skip duplicate message {envelope.message_id}")
            return
        envelope = self._apply_small_group_auto_reply(envelope)
        route = route_message(envelope)
        if not route.should_handle:
            print(f"Ignore message {envelope.message_id}: {route.reason}")
            return
        topic_reply = handle_topic_command(self.store, route, envelope.text)
        if topic_reply is not None:
            self._reply_text(envelope.message_id, topic_reply)
            return
        topic = self.store.resolve_topic(route, envelope, self.config.topic_idle_seconds)
        route = topic.route
        self.store.upsert_session(route.session_key, route.reason, envelope.text[:60])
        self.store.record_user_message(route.session_key, envelope)
        if not self._reply_topic_notice(envelope.message_id, topic.notice):
            self._reply_ack(envelope.message_id)

        command_reply = handle_command(self.store, route, envelope.text, self.config)
        if command_reply is not None:
            self._reply_text(envelope.message_id, command_reply)
            self.store.record_reply(envelope.message_id, command_reply)
            return

        thread_id = self.store.get_thread_id(route.session_key)
        prompt = build_prompt(envelope, route, self.config)
        self.store.begin_task(route.session_key)
        worker = threading.Thread(
            target=self._run_codex_and_reply,
            args=(envelope, route, thread_id, prompt),
            daemon=True,
        )
        try:
            worker.start()
        except Exception:
            self.store.finish_task(route.session_key)
            raise

    def _run_codex_and_reply(
        self,
        envelope: MessageEnvelope,
        route: RouteDecision,
        thread_id: str | None,
        prompt: str,
    ) -> None:
        started_at = time.monotonic()
        progress_stop = threading.Event()
        progress_thread = self._start_progress_notifier(envelope, route, started_at, progress_stop)
        try:
            new_thread_id, reply = self.runner.run(prompt, thread_id)
            if new_thread_id and new_thread_id != thread_id:
                self.store.set_thread_id(route.session_key, new_thread_id)
            reply = reply or "Codex 没有返回内容。"
        except Exception as exc:
            reply = f"Codex 执行失败：{exc}"
        finally:
            progress_stop.set()
            if progress_thread:
                progress_thread.join(timeout=1)
            self.store.finish_task(route.session_key)
        try:
            delivered_reply = self._deliver_codex_reply(envelope, route, reply)
            self.store.record_reply(envelope.message_id, delivered_reply)
        except Exception as exc:
            print(f"Final Feishu reply failed for {envelope.message_id}: {exc}", flush=True)
        elapsed = time.monotonic() - started_at
        print(f"Handled message {envelope.message_id} in {elapsed:.1f}s", flush=True)

    def _deliver_codex_reply(self, envelope: MessageEnvelope, route: RouteDecision, raw_reply: str) -> str:
        text = raw_reply.strip()
        cards: list[dict[str, Any]] = []
        notices: list[str] = []
        errors: list[str] = []

        if self.config.codex_cards_enabled:
            text, cards, card_errors = extract_feishu_cards(text)
            errors.extend(card_errors)

        text, docs, doc_errors = extract_feishu_docs(text)
        errors.extend(doc_errors)
        for doc_request in docs:
            if not self.doc_client:
                errors.append(f"飞书文档能力未启用，未创建「{doc_request.title}」。")
                continue
            try:
                created = self.doc_client.create_document(doc_request)
                notices.append(f"已创建飞书文档：{created.title}\n{created.url}")
            except Exception as exc:
                errors.append(f"飞书文档「{doc_request.title}」创建失败：{exc}")

        if (
            self.doc_client
            and self.config.docs_auto_min_chars > 0
            and len(text) > self.config.docs_auto_min_chars
            and not docs
        ):
            title = compact_title(envelope.text, "Codex 长回复")
            try:
                created = self.doc_client.create_document(FeishuDocRequest(title, text))
                summary = truncate_reply(text, min(self.config.reply_max_chars, 1200))
                text = f"{summary}\n\n完整内容已放入飞书文档：\n{created.url}"
            except Exception as exc:
                errors.append(f"长回复自动转飞书文档失败：{exc}")

        if notices:
            text = "\n\n".join(part for part in [text, *notices] if part)
        if errors:
            text = "\n\n".join(part for part in [text, *errors] if part)

        delivered_parts: list[str] = []
        if text.strip():
            reply_text = truncate_reply(text, self.config.reply_max_chars)
            self._reply_text(envelope.message_id, reply_text)
            delivered_parts.append(reply_text)
        elif not cards:
            self._reply_text(envelope.message_id, "Codex 没有返回内容。")
            delivered_parts.append("Codex 没有返回内容。")

        for card in cards:
            stamped = stamp_codex_card_callbacks(card, route, envelope.message_id)
            if self._reply_interactive(envelope.message_id, stamped):
                delivered_parts.append("[已发送飞书卡片]")
            else:
                failure = "飞书卡片发送失败，已保留在本机 Codex 会话。"
                self._reply_text(envelope.message_id, failure)
                delivered_parts.append(failure)

        return "\n\n".join(delivered_parts).strip()

    def _start_progress_notifier(
        self,
        envelope: MessageEnvelope,
        route: RouteDecision,
        started_at: float,
        stop_event: threading.Event,
    ) -> threading.Thread | None:
        interval = self.config.task_progress_seconds
        if interval <= 0:
            return None

        def notify() -> None:
            while not stop_event.wait(interval):
                elapsed = format_duration(time.monotonic() - started_at)
                text = (
                    f"任务仍在执行中，已运行 {elapsed}。\n"
                    "完成后会继续在这里返回最终结果。"
                )
                try:
                    self._reply_text(envelope.message_id, text)
                except Exception as exc:
                    print(
                        f"Progress reply failed for {envelope.message_id} ({route.session_key}): {exc}",
                        flush=True,
                    )

        thread = threading.Thread(target=notify, daemon=True)
        thread.start()
        return thread

    def _is_stale(self, envelope: MessageEnvelope) -> bool:
        if not envelope.create_time_ms or self.config.ignore_older_than_seconds <= 0:
            return False
        return time.time() - (envelope.create_time_ms / 1000) > self.config.ignore_older_than_seconds

    def _reply_text(self, message_id: str, text: str) -> None:
        content = json.dumps({"text": text}, ensure_ascii=False)
        request = (
            self.reply_request.builder()
            .message_id(message_id)
            .request_body(self.reply_body.builder().msg_type("text").content(content).build())
            .build()
        )
        response = self.api_client.im.v1.message.reply(request)
        success = getattr(response, "success", None)
        if callable(success) and success():
            return
        print(f"Feishu reply failed: code={getattr(response, 'code', '')} msg={getattr(response, 'msg', '')}")

    def _reply_ack(self, message_id: str) -> None:
        if self.config.ack_text:
            self._reply_text(message_id, self.config.ack_text)

    def _reply_topic_notice(self, message_id: str, notice: TopicNotice | None) -> bool:
        if not notice or not self.config.topic_notice_enabled:
            return False
        return self._reply_interactive(message_id, build_topic_notice_card(notice))

    def _reply_interactive(self, message_id: str, card: dict[str, Any]) -> bool:
        try:
            content = self._interactive_content(card)
        except Exception as exc:
            print(f"Feishu card build failed: {exc}", flush=True)
            return False
        request = (
            self.reply_request.builder()
            .message_id(message_id)
            .request_body(self.reply_body.builder().msg_type("interactive").content(content).build())
            .build()
        )
        response = self.api_client.im.v1.message.reply(request)
        success = getattr(response, "success", None)
        if callable(success) and success():
            return True
        print(f"Feishu card reply failed: code={getattr(response, 'code', '')} msg={getattr(response, 'msg', '')}")
        return False

    def _send_interactive_message(self, chat_id: str, card: dict[str, Any]) -> bool:
        if not self.api_client:
            return False
        try:
            content = self._interactive_content(card)
        except Exception as exc:
            print(f"Feishu proactive card build failed: {exc}", flush=True)
            return False
        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
        except Exception as exc:
            print(f"Feishu create message import failed: {exc}", flush=True)
            return False
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(content)
                .uuid(str(uuid.uuid4()))
                .build()
            )
            .build()
        )
        response = self.api_client.im.v1.message.create(request)
        success = getattr(response, "success", None)
        if callable(success) and success():
            return True
        print(f"Feishu card send failed: code={getattr(response, 'code', '')} msg={getattr(response, 'msg', '')}")
        return False

    def _interactive_content(self, card: dict[str, Any]) -> str:
        if card.get("schema") == "2.0" and self.config.cardkit_enabled:
            card_id = self.open_api.create_card_id(card)
            return json.dumps({"type": "card", "data": {"card_id": card_id}}, ensure_ascii=False)
        return json.dumps(card, ensure_ascii=False)

    def handle_card_action(self, data: Any) -> Any:
        action = extract_card_action_value(data)
        if action.get("bridge_action") == CARD_ACTION_NAME:
            return self._handle_codex_card_action(data, action)
        if action.get("bridge_action") != "topic_boundary":
            return topic_action_response("已收到。")
        base_key = str(action.get("base_session_key", "") or "")
        if not base_key:
            return topic_action_response("缺少话题信息。", "error")
        choice = action.get("choice")
        if choice == "continue_previous":
            expected_active = str(action.get("active_session_key", "") or "") or None
            restored = self.store.restore_previous_topic(base_key, expected_active)
            if restored:
                return topic_action_response("已切回上个话题。")
            return topic_action_response("话题状态已变化，未切换。", "warning")
        if choice == "keep_current":
            self.store.keep_current_topic(base_key)
            return topic_action_response("保持新话题。")
        return topic_action_response("未知操作。", "error")

    def _handle_codex_card_action(self, data: Any, action: dict[str, Any]) -> Any:
        session_key = str(action.get("session_key", "") or "")
        if not session_key:
            return topic_action_response("缺少会话信息。", "error")
        reply_to_message_id = str(action.get("origin_message_id", "") or "") or extract_card_action_message_id(data)
        if not reply_to_message_id:
            return topic_action_response("缺少原始消息，无法回发结果。", "error")
        chat_id, chat_type, sender_id, sender_name = extract_card_action_chat(data)
        payload = card_action_payload(action, extract_card_action_form_value(data))
        route = RouteDecision(session_key=session_key, should_handle=True, reason="card-action")
        envelope = MessageEnvelope(
            message_id=reply_to_message_id,
            chat_id=chat_id,
            chat_type=chat_type,
            message_type="card_action",
            text="[card-click] " + json.dumps(payload, ensure_ascii=False),
            root_id="",
            parent_id="",
            thread_id="",
            create_time_ms=int(time.time() * 1000),
            addressed=True,
            sender_id=sender_id,
            sender_name=sender_name,
        )
        self.store.upsert_session(session_key, "card-action", "card action")
        thread_id = self.store.get_thread_id(session_key)
        prompt = build_card_action_prompt(envelope, route, payload, self.config)
        self.store.begin_task(session_key)
        worker = threading.Thread(
            target=self._run_codex_and_reply,
            args=(envelope, route, thread_id, prompt),
            daemon=True,
        )
        try:
            worker.start()
        except Exception:
            self.store.finish_task(session_key)
            raise
        return topic_action_response("收到，继续处理。")


def run_listener(config: BridgeConfig) -> None:
    if not config.app_id or not config.app_secret:
        raise SystemExit("Missing FEISHU_APP_ID or FEISHU_APP_SECRET.")
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        P2ImMessageMessageReadV1,
        P2ImMessageReceiveV1,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )

    api_client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
    bridge = FeishuCodexBridge(config, api_client, ReplyMessageRequest, ReplyMessageRequestBody)
    bridge.start_idle_topic_notifier()

    def on_message(data: P2ImMessageReceiveV1) -> None:
        bridge.handle_message(data)

    def on_message_read(data: P2ImMessageMessageReadV1) -> None:
        return None

    def on_card_action(data: Any) -> Any:
        return bridge.handle_card_action(data)

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_im_message_message_read_v1(on_message_read)
        .register_p2_card_action_trigger(on_card_action)
        .build()
    )
    ws_client = lark.ws.Client(config.app_id, config.app_secret, event_handler=event_handler)
    print(f"Feishu Codex bridge is running. DB: {config.db_path}")
    ws_client.start()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    parser.add_argument("--once-route", help="Print the route key for a sample text and exit.")
    args = parser.parse_args()
    if args.version:
        print(VERSION)
        return 0
    if args.once_route:
        envelope = MessageEnvelope(
            message_id="sample",
            chat_id="chat",
            chat_type="p2p",
            message_type="text",
            text=args.once_route,
            root_id="",
            parent_id="",
            thread_id="",
            create_time_ms=None,
            addressed=True,
        )
        print(route_message(envelope))
        return 0
    run_listener(BridgeConfig.from_env())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
