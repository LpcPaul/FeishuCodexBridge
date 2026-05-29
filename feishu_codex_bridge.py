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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_DIR = Path.home() / "Library" / "Application Support" / "FeishuCodexBridge"
DEFAULT_WORKDIR = Path.home()
VERSION = "0.1.0"
DEFAULT_TOPIC_IDLE_SECONDS = 2 * 60 * 60
DEFAULT_TASK_PROGRESS_SECONDS = 2 * 60 * 60
DEFAULT_ACK_TEXT = "收到，我要开始干活了，稍等我"
MOBILE_REPLY_CONTEXT = (
    "你正在通过手机通信软件回复用户。请使用移动端可读格式：\n"
    "先给结论/摘要/判断；短段落；根据消息类型组织内容；\n"
    "不要输出大段长文；如果内容较长，只回复第一层结论/摘要/判断，用户要求详细答复时再展开。"
)


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
    task_progress_seconds: float = DEFAULT_TASK_PROGRESS_SECONDS

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
            task_progress_seconds=_float_env("FEISHU_TASK_PROGRESS_SECONDS", DEFAULT_TASK_PROGRESS_SECONDS),
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
                    topic_started_at text not null,
                    updated_at text not null
                );
                """
            )
            self._ensure_column(con, "topic_states", "active_task_count", "integer not null default 0")
            self._ensure_column(con, "topic_states", "last_task_completed_ms", "integer")

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
                    set last_message_ms = ?, updated_at = ?
                    where base_session_key = ?
                    """,
                    (now_ms, now, route.session_key),
                )
                return TopicResolution(replace_route(route, active_key, "active-task"))
            if last_message_ms and now_ms - last_message_ms > idle_seconds * 1000:
                topic_seq = int(row["topic_seq"] or 1) + 1
                new_key = f"{route.session_key}:topic:{topic_seq}"
                con.execute(
                    """
                    update topic_states
                    set active_session_key = ?,
                        previous_session_key = ?,
                        topic_seq = ?,
                        last_message_ms = ?,
                        topic_started_at = ?,
                        updated_at = ?
                    where base_session_key = ?
                    """,
                    (new_key, active_key, topic_seq, now_ms, now, now, route.session_key),
                )
                self._upsert_session_on_connection(con, new_key, "auto-new-topic", envelope.text[:60])
                return TopicResolution(
                    route=replace_route(route, new_key, "auto-new-topic"),
                    notice=TopicNotice(
                        base_session_key=route.session_key,
                        previous_session_key=active_key,
                        active_session_key=new_key,
                        idle_seconds=idle_seconds,
                    ),
                )

            con.execute(
                """
                update topic_states
                set last_message_ms = ?, updated_at = ?
                where base_session_key = ?
                """,
                (now_ms, now, route.session_key),
            )
            return TopicResolution(replace_route(route, active_key, route.reason))

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
                set active_session_key = ?, previous_session_key = ?, last_message_ms = ?, updated_at = ?
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
                    updated_at = ?
                where active_session_key = ? or base_session_key = ?
                """,
                (now_ms, now_ms, now, session_key, session_key),
            )

    def keep_current_topic(self, base_session_key: str) -> str | None:
        with self.connect() as con:
            row = con.execute(
                "select active_session_key from topic_states where base_session_key = ?",
                (base_session_key,),
            ).fetchone()
            return str(row["active_session_key"]) if row and row["active_session_key"] else None

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


def replace_route(route: RouteDecision, session_key: str, reason: str) -> RouteDecision:
    return RouteDecision(
        session_key=session_key,
        should_handle=route.should_handle,
        reason=reason,
        starts_new_container=route.starts_new_container,
    )


def build_prompt(envelope: MessageEnvelope, route: RouteDecision) -> str:
    return (
        "这是一条从飞书转发到 Codex 的用户消息。\n"
        "按当前工作区规则处理；不要把飞书入口绑定到任何单一技能或工具。\n"
        "如果用户明确要求调研、读取链接、编辑文件或运行工具，你可以按 Codex 正常能力选择合适方法。\n\n"
        f"{MOBILE_REPLY_CONTEXT}\n\n"
        f"飞书来源：chat_type={envelope.chat_type}, route={route.reason}\n"
        f"会话键：{route.session_key}\n\n"
        f"用户消息：\n{envelope.text.strip()}"
    )


def handle_command(store: StateStore, route: RouteDecision, text: str) -> str | None:
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
            "title": {"tag": "plain_text", "content": "已开启新话题"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"距离上次对话已超过 {hours} 小时，本轮默认不会带入上个话题。\n"
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

        command_reply = handle_command(self.store, route, envelope.text)
        if command_reply is not None:
            self._reply_text(envelope.message_id, command_reply)
            self.store.record_reply(envelope.message_id, command_reply)
            return

        thread_id = self.store.get_thread_id(route.session_key)
        prompt = build_prompt(envelope, route)
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
            reply = truncate_reply(reply or "Codex 没有返回内容。", self.config.reply_max_chars)
        except Exception as exc:
            reply = f"Codex 执行失败：{exc}"
        finally:
            progress_stop.set()
            if progress_thread:
                progress_thread.join(timeout=1)
            self.store.finish_task(route.session_key)
        try:
            self._reply_text(envelope.message_id, reply)
            self.store.record_reply(envelope.message_id, reply)
        except Exception as exc:
            print(f"Final Feishu reply failed for {envelope.message_id}: {exc}", flush=True)
        elapsed = time.monotonic() - started_at
        print(f"Handled message {envelope.message_id} in {elapsed:.1f}s", flush=True)

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
        content = json.dumps(card, ensure_ascii=False)
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

    def handle_card_action(self, data: Any) -> Any:
        action = getattr(getattr(getattr(data, "event", None), "action", None), "value", None) or {}
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


def run_listener(config: BridgeConfig) -> None:
    if not config.app_id or not config.app_secret:
        raise SystemExit("Missing FEISHU_APP_ID or FEISHU_APP_SECRET.")
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1, ReplyMessageRequest, ReplyMessageRequestBody

    api_client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
    bridge = FeishuCodexBridge(config, api_client, ReplyMessageRequest, ReplyMessageRequestBody)

    def on_message(data: P2ImMessageReceiveV1) -> None:
        bridge.handle_message(data)

    def on_card_action(data: Any) -> Any:
        return bridge.handle_card_action(data)

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
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
