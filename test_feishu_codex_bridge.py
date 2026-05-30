import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from feishu_codex_bridge import (
    BridgeConfig,
    CodexRunner,
    FeishuDocumentResult,
    FeishuDocRequest,
    FeishuCodexBridge,
    MessageEnvelope,
    MOBILE_REPLY_CONTEXT,
    StateStore,
    build_prompt,
    extract_message_text,
    extract_feishu_cards,
    parse_last_agent_message,
    parse_thread_id,
    route_message,
    stamp_codex_card_callbacks,
)


class FeishuCodexBridgeTests(unittest.TestCase):
    def test_p2p_routes_to_default_session(self):
        envelope = MessageEnvelope("m1", "c1", "p2p", "text", "你好", "", "", "", None, True)
        decision = route_message(envelope)
        self.assertTrue(decision.should_handle)
        self.assertEqual(decision.session_key, "feishu:p2p:c1")

    def test_group_thread_routes_to_thread_session(self):
        envelope = MessageEnvelope("m2", "c1", "group", "text", "继续", "root1", "", "", None, False)
        decision = route_message(envelope)
        self.assertTrue(decision.should_handle)
        self.assertEqual(decision.session_key, "feishu:c1:thread:root1")

    def test_group_mention_starts_new_session(self):
        envelope = MessageEnvelope("m3", "c1", "group", "text", "@Codex 做一下", "", "", "", None, True)
        decision = route_message(envelope)
        self.assertTrue(decision.should_handle)
        self.assertTrue(decision.starts_new_container)
        self.assertEqual(decision.session_key, "feishu:c1:thread:m3")

    def test_group_plain_message_is_ignored(self):
        envelope = MessageEnvelope("m4", "c1", "group", "text", "普通聊天", "", "", "", None, False)
        decision = route_message(envelope)
        self.assertFalse(decision.should_handle)

    def test_small_group_plain_message_routes_to_group_direct_session(self):
        envelope = MessageEnvelope("m4", "c1", "group", "text", "普通聊天", "", "", "", None, False, group_auto_reply=True)
        decision = route_message(envelope)
        self.assertTrue(decision.should_handle)
        self.assertFalse(decision.starts_new_container)
        self.assertEqual(decision.reason, "small-group-direct")
        self.assertEqual(decision.session_key, "feishu:c1:direct")

    def test_extract_text_from_lark_payload(self):
        event = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps({"text": "@Codex 你好"}, ensure_ascii=False)
                )
            )
        )
        self.assertEqual(extract_message_text(event), "你好")

    def test_parse_codex_json_events(self):
        stdout = '\n'.join(
            [
                '{"type":"thread.started","thread_id":"abc"}',
                '{"type":"item.completed","item":{"type":"agent_message","text":"OK"}}',
            ]
        )
        self.assertEqual(parse_thread_id(stdout), "abc")
        self.assertEqual(parse_last_agent_message(stdout), "OK")

    def test_build_prompt_includes_mobile_reply_context(self):
        envelope = MessageEnvelope("m1", "c1", "p2p", "text", "帮我总结一下", "", "", "", None, True)
        route = route_message(envelope)
        prompt = build_prompt(envelope, route)

        self.assertIn(MOBILE_REPLY_CONTEXT, prompt)
        self.assertIn("用户消息：\n帮我总结一下", prompt)

    def test_direct_chat_message_keeps_active_topic_after_idle_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.sqlite")
            route = route_message(MessageEnvelope("m1", "c1", "p2p", "text", "第一件事", "", "", "", 1_000, True))
            first = store.resolve_topic(route, MessageEnvelope("m1", "c1", "p2p", "text", "第一件事", "", "", "", 1_000, True), 7200)
            self.assertEqual(first.route.session_key, "feishu:p2p:c1")
            self.assertIsNone(first.notice)

            later = store.resolve_topic(
                route,
                MessageEnvelope("m2", "c1", "p2p", "text", "另一件事", "", "", "", 7_201_001, True),
                7200,
            )
            self.assertEqual(later.route.session_key, "feishu:p2p:c1")
            self.assertIsNone(later.notice)

    def test_idle_notice_scan_starts_new_topic_after_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.sqlite")
            route = route_message(MessageEnvelope("m1", "c1", "p2p", "text", "第一件事", "", "", "", 1_000, True))
            store.resolve_topic(route, MessageEnvelope("m1", "c1", "p2p", "text", "第一件事", "", "", "", 1_000, True), 7200)

            notices = store.claim_due_topic_notices(7200, now_ms=7_201_001)

            self.assertEqual(len(notices), 1)
            self.assertEqual(notices[0].chat_id, "c1")
            self.assertEqual(notices[0].notice.previous_session_key, "feishu:p2p:c1")
            self.assertEqual(notices[0].notice.active_session_key, "feishu:p2p:c1:topic:2")
            later = store.resolve_topic(
                route,
                MessageEnvelope("m2", "c1", "p2p", "text", "另一件事", "", "", "", 7_202_000, True),
                7200,
            )
            self.assertEqual(later.route.session_key, "feishu:p2p:c1:topic:2")

    def test_restore_previous_topic_switches_active_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.sqlite")
            route = route_message(MessageEnvelope("m1", "c1", "p2p", "text", "第一件事", "", "", "", 1_000, True))
            store.resolve_topic(route, MessageEnvelope("m1", "c1", "p2p", "text", "第一件事", "", "", "", 1_000, True), 7200)
            store.claim_due_topic_notices(7200, now_ms=7_201_001)

            self.assertEqual(store.restore_previous_topic("feishu:p2p:c1"), "feishu:p2p:c1")
            restored = store.resolve_topic(
                route,
                MessageEnvelope("m3", "c1", "p2p", "text", "继续", "", "", "", 7_202_000, True),
                7200,
            )
            self.assertEqual(restored.route.session_key, "feishu:p2p:c1")

    def test_running_task_blocks_idle_topic_switch_until_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.sqlite")
            route = route_message(MessageEnvelope("m1", "c1", "p2p", "text", "长任务", "", "", "", 1_000, True))
            store.resolve_topic(route, MessageEnvelope("m1", "c1", "p2p", "text", "长任务", "", "", "", 1_000, True), 7200)
            store.begin_task("feishu:p2p:c1", started_ms=1_000)

            during = store.resolve_topic(
                route,
                MessageEnvelope("m2", "c1", "p2p", "text", "补充一下", "", "", "", 7_201_001, True),
                7200,
            )
            self.assertEqual(during.route.session_key, "feishu:p2p:c1")
            self.assertIsNone(during.notice)
            self.assertEqual(store.claim_due_topic_notices(7200, now_ms=7_201_001), [])

            completed_ms = 8 * 60 * 60 * 1000
            store.finish_task("feishu:p2p:c1", completed_ms=completed_ms)
            one_hour_later = store.resolve_topic(
                route,
                MessageEnvelope("m3", "c1", "p2p", "text", "继续", "", "", "", completed_ms + 3_600_000, True),
                7200,
            )
            self.assertEqual(one_hour_later.route.session_key, "feishu:p2p:c1")

            notices = store.claim_due_topic_notices(7200, now_ms=completed_ms + 10_800_001)
            self.assertEqual(len(notices), 1)
            self.assertEqual(notices[0].notice.active_session_key, "feishu:p2p:c1:topic:2")

            three_hours_later = store.resolve_topic(
                route,
                MessageEnvelope("m4", "c1", "p2p", "text", "新事", "", "", "", completed_ms + 10_801_000, True),
                7200,
            )
            self.assertEqual(three_hours_later.route.session_key, "feishu:p2p:c1:topic:2")

    def test_codex_command_uses_node_for_js_entrypoint(self):
        config = BridgeConfig("app", "secret", codex_bin="/opt/codex/bin/codex.js", node_bin="/opt/node/bin/node")
        self.assertEqual(CodexRunner(config)._codex_command(), ["/opt/node/bin/node", "/opt/codex/bin/codex.js"])

    def test_codex_command_resolves_symlink_to_js_when_node_bin_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            js_path = tmp_path / "codex.js"
            js_path.write_text("#!/usr/bin/env node\n", encoding="utf-8")
            link_path = tmp_path / "codex"
            link_path.symlink_to(js_path)

            config = BridgeConfig("app", "secret", codex_bin=str(link_path), node_bin="/opt/node/bin/node")

            self.assertEqual(CodexRunner(config)._codex_command(), ["/opt/node/bin/node", str(js_path.resolve())])

    def test_codex_runner_does_not_set_bridge_timeout(self):
        captured_kwargs = {}

        def fake_run(command, **kwargs):
            captured_kwargs.update(kwargs)
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_text("最终回复", encoding="utf-8")
            return SimpleNamespace(
                stdout='{"type":"thread.started","thread_id":"thread-no-timeout"}\n',
                stderr="",
                returncode=0,
            )

        with tempfile.TemporaryDirectory() as tmp:
            config = BridgeConfig("app", "secret", workdir=Path(tmp), codex_bin="/usr/local/bin/codex")
            with patch("feishu_codex_bridge.subprocess.run", fake_run):
                thread_id, reply = CodexRunner(config).run("跑一个任务", None)

        self.assertNotIn("timeout", captured_kwargs)
        self.assertEqual(thread_id, "thread-no-timeout")
        self.assertEqual(reply, "最终回复")

    def test_handle_message_replies_ack_before_running_codex(self):
        events = []
        runner_started = threading.Event()
        release_runner = threading.Event()
        final_replied = threading.Event()

        class FakeRunner:
            def run(self, prompt, thread_id):
                events.append(("run", thread_id))
                runner_started.set()
                assert release_runner.wait(1)
                return "thread-1", "最终回复"

        with tempfile.TemporaryDirectory() as tmp:
            config = BridgeConfig("app", "secret", runtime_dir=Path(tmp), ack_text="收到，我要开始干活了，稍等我")
            bridge = FeishuCodexBridge(config, None, None, None)
            bridge.runner = FakeRunner()

            def fake_reply(message_id, text):
                events.append(("reply", text))
                if text == "最终回复":
                    final_replied.set()

            bridge._reply_text = fake_reply

            data = SimpleNamespace(
                event=SimpleNamespace(
                    message=SimpleNamespace(
                        message_id="m-ack",
                        chat_id="c1",
                        chat_type="p2p",
                        message_type="text",
                        content=json.dumps({"text": "你好"}, ensure_ascii=False),
                        root_id="",
                        parent_id="",
                        thread_id="",
                        create_time="",
                        mentions=None,
                    )
                )
            )

            bridge.handle_message(data)
            self.assertGreaterEqual(len(events), 1)
            self.assertEqual(events[0], ("reply", "收到，我要开始干活了，稍等我"))
            self.assertTrue(runner_started.wait(1))
            release_runner.set()
            self.assertTrue(final_replied.wait(1))
            time.sleep(0.05)

        self.assertEqual(events, [("reply", "收到，我要开始干活了，稍等我"), ("run", None), ("reply", "最终回复")])

    def test_handle_message_auto_replies_in_small_group(self):
        events = []
        runner_started = threading.Event()
        final_replied = threading.Event()

        class FakeOpenAPI:
            def __init__(self):
                self.chat_ids = []

            def chat_human_member_count(self, chat_id):
                self.chat_ids.append(chat_id)
                return 1

        class FakeRunner:
            def run(self, prompt, thread_id):
                events.append(("run", thread_id, "small-group-direct" in prompt))
                runner_started.set()
                return "thread-small-group", "小群回复"

        with tempfile.TemporaryDirectory() as tmp:
            config = BridgeConfig(
                "app",
                "secret",
                runtime_dir=Path(tmp),
                ack_text="收到，我要开始干活了，稍等我",
                ignore_older_than_seconds=0,
            )
            bridge = FeishuCodexBridge(config, None, None, None)
            fake_open_api = FakeOpenAPI()
            bridge.open_api = fake_open_api
            bridge.runner = FakeRunner()

            def fake_reply(message_id, text):
                events.append(("reply", text))
                if text == "小群回复":
                    final_replied.set()

            bridge._reply_text = fake_reply
            data = SimpleNamespace(
                event=SimpleNamespace(
                    message=SimpleNamespace(
                        message_id="m-small-group",
                        chat_id="c-small",
                        chat_type="group",
                        message_type="text",
                        content=json.dumps({"text": "普通聊天"}, ensure_ascii=False),
                        root_id="",
                        parent_id="",
                        thread_id="",
                        create_time="",
                        mentions=None,
                    )
                )
            )

            bridge.handle_message(data)
            self.assertTrue(runner_started.wait(1))
            self.assertTrue(final_replied.wait(1))
            time.sleep(0.05)

            self.assertEqual(fake_open_api.chat_ids, ["c-small"])
            self.assertEqual(bridge.store.get_thread_id("feishu:c-small:direct"), "thread-small-group")

        self.assertEqual(
            events,
            [
                ("reply", "收到，我要开始干活了，稍等我"),
                ("run", None, True),
                ("reply", "小群回复"),
            ],
        )

    def test_handle_message_does_not_send_topic_notice_after_idle_timeout(self):
        events = []
        runner_started = threading.Event()
        final_replied = threading.Event()

        class FakeRunner:
            def run(self, prompt, thread_id):
                events.append(("run", thread_id, "auto-new-topic" in prompt))
                runner_started.set()
                return "thread-1", "原话题回复"

        with tempfile.TemporaryDirectory() as tmp:
            config = BridgeConfig(
                "app",
                "secret",
                runtime_dir=Path(tmp),
                ack_text="收到，我要开始干活了，稍等我",
                ignore_older_than_seconds=0,
            )
            bridge = FeishuCodexBridge(config, None, None, None)
            bridge.runner = FakeRunner()
            store = bridge.store
            route = route_message(MessageEnvelope("m-old", "c1", "p2p", "text", "旧话题", "", "", "", 1_000, True))
            store.resolve_topic(route, MessageEnvelope("m-old", "c1", "p2p", "text", "旧话题", "", "", "", 1_000, True), 7200)

            def fake_reply(message_id, text):
                events.append(("reply", text))
                if text == "原话题回复":
                    final_replied.set()

            def fake_card(message_id, card):
                events.append(("card", card["header"]["title"]["content"]))
                return True

            bridge._reply_text = fake_reply
            bridge._reply_interactive = fake_card

            data = SimpleNamespace(
                event=SimpleNamespace(
                    message=SimpleNamespace(
                        message_id="m-new",
                        chat_id="c1",
                        chat_type="p2p",
                        message_type="text",
                        content=json.dumps({"text": "新话题"}, ensure_ascii=False),
                        root_id="",
                        parent_id="",
                        thread_id="",
                        create_time=str(7202),
                        mentions=None,
                    )
                )
            )

            bridge.handle_message(data)
            self.assertTrue(runner_started.wait(1))
            self.assertTrue(final_replied.wait(1))
            time.sleep(0.05)

        self.assertEqual(events[0], ("reply", "收到，我要开始干活了，稍等我"))
        self.assertNotIn(("card", "已进入新话题"), events)
        self.assertIn(("run", None, False), events)
        self.assertIn(("reply", "原话题回复"), events)

    def test_idle_topic_notifier_sends_proactive_card(self):
        events = []

        with tempfile.TemporaryDirectory() as tmp:
            config = BridgeConfig("app", "secret", runtime_dir=Path(tmp), ignore_older_than_seconds=0)
            bridge = FeishuCodexBridge(config, None, None, None)
            route = route_message(MessageEnvelope("m-old", "c1", "p2p", "text", "旧话题", "", "", "", 1_000, True))
            bridge.store.resolve_topic(
                route,
                MessageEnvelope("m-old", "c1", "p2p", "text", "旧话题", "", "", "", 1_000, True),
                7200,
            )

            def fake_send(chat_id, card):
                events.append(("send-card", chat_id, card["header"]["title"]["content"]))
                return True

            bridge._send_interactive_message = fake_send

            sent = bridge._send_due_idle_topic_notices(now_ms=7_201_001)
            routed = bridge.store.resolve_topic(
                route,
                MessageEnvelope("m-new", "c1", "p2p", "text", "新话题", "", "", "", 7_202_000, True),
                7200,
            )

        self.assertEqual(sent, 1)
        self.assertEqual(events, [("send-card", "c1", "已进入新话题")])
        self.assertEqual(routed.route.session_key, "feishu:p2p:c1:topic:2")

    def test_long_running_task_sends_progress_until_final_reply(self):
        events = []
        runner_started = threading.Event()
        progress_sent = threading.Event()
        release_runner = threading.Event()
        final_replied = threading.Event()

        class FakeRunner:
            def run(self, prompt, thread_id):
                events.append(("run", thread_id))
                runner_started.set()
                assert release_runner.wait(1)
                return "thread-long", "最终完成"

        with tempfile.TemporaryDirectory() as tmp:
            config = BridgeConfig(
                "app",
                "secret",
                runtime_dir=Path(tmp),
                ack_text="收到，我要开始干活了，稍等我",
                task_progress_seconds=0.05,
            )
            bridge = FeishuCodexBridge(config, None, None, None)
            bridge.runner = FakeRunner()

            def fake_reply(message_id, text):
                events.append(("reply", text))
                if text.startswith("任务仍在执行中"):
                    progress_sent.set()
                if text == "最终完成":
                    final_replied.set()

            bridge._reply_text = fake_reply
            data = SimpleNamespace(
                event=SimpleNamespace(
                    message=SimpleNamespace(
                        message_id="m-long",
                        chat_id="c1",
                        chat_type="p2p",
                        message_type="text",
                        content=json.dumps({"text": "跑一个长任务"}, ensure_ascii=False),
                        root_id="",
                        parent_id="",
                        thread_id="",
                        create_time="",
                        mentions=None,
                    )
                )
            )

            bridge.handle_message(data)
            self.assertTrue(runner_started.wait(1))
            self.assertTrue(progress_sent.wait(1))
            release_runner.set()
            self.assertTrue(final_replied.wait(1))
            time.sleep(0.05)

        self.assertEqual(events[0], ("reply", "收到，我要开始干活了，稍等我"))
        self.assertIn(("run", None), events)
        self.assertIn(("reply", "最终完成"), events)

    def test_runner_exception_always_replies_failure(self):
        events = []
        final_replied = threading.Event()

        class FakeRunner:
            def run(self, prompt, thread_id):
                raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            config = BridgeConfig("app", "secret", runtime_dir=Path(tmp), task_progress_seconds=0)
            bridge = FeishuCodexBridge(config, None, None, None)
            bridge.runner = FakeRunner()

            def fake_reply(message_id, text):
                events.append(("reply", text))
                if text.startswith("Codex 执行失败：boom"):
                    final_replied.set()

            bridge._reply_text = fake_reply
            data = SimpleNamespace(
                event=SimpleNamespace(
                    message=SimpleNamespace(
                        message_id="m-fail",
                        chat_id="c1",
                        chat_type="p2p",
                        message_type="text",
                        content=json.dumps({"text": "会失败的任务"}, ensure_ascii=False),
                        root_id="",
                        parent_id="",
                        thread_id="",
                        create_time="",
                        mentions=None,
                    )
                )
            )

            bridge.handle_message(data)
            self.assertTrue(final_replied.wait(1))
            time.sleep(0.05)

        self.assertIn(("reply", "Codex 执行失败：boom"), events)

    def test_card_action_restores_previous_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = BridgeConfig("app", "secret", runtime_dir=Path(tmp))
            bridge = FeishuCodexBridge(config, None, None, None)
            route = route_message(MessageEnvelope("m1", "c1", "p2p", "text", "旧话题", "", "", "", 1_000, True))
            bridge.store.resolve_topic(route, MessageEnvelope("m1", "c1", "p2p", "text", "旧话题", "", "", "", 1_000, True), 7200)
            bridge.store.claim_due_topic_notices(7200, now_ms=7_201_001)

            data = SimpleNamespace(
                event=SimpleNamespace(
                    action=SimpleNamespace(
                        value={
                            "bridge_action": "topic_boundary",
                            "choice": "continue_previous",
                            "base_session_key": "feishu:p2p:c1",
                            "active_session_key": "feishu:p2p:c1:topic:2",
                        }
                    )
                )
            )

            bridge.handle_card_action(data)
            restored = bridge.store.resolve_topic(
                route,
                MessageEnvelope("m3", "c1", "p2p", "text", "继续", "", "", "", 7_202_000, True),
                7200,
            )
            self.assertEqual(restored.route.session_key, "feishu:p2p:c1")

    def test_extract_feishu_card_block(self):
        text, cards, errors = extract_feishu_cards(
            '请看卡片\n```feishu-card\n{"config": {"wide_screen_mode": true}, "elements": []}\n```'
        )

        self.assertEqual(text, "请看卡片")
        self.assertEqual(cards, [{"config": {"wide_screen_mode": True}, "elements": []}])
        self.assertEqual(errors, [])

    def test_stamp_codex_card_callbacks_adds_session_metadata(self):
        route = route_message(MessageEnvelope("m1", "c1", "p2p", "text", "选一个", "", "", "", None, True))
        card = {
            "elements": [
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "A"},
                            "value": {"choice": "a"},
                        }
                    ],
                }
            ]
        }

        stamped = stamp_codex_card_callbacks(card, route, "m1")
        value = stamped["elements"][0]["actions"][0]["value"]

        self.assertEqual(value["bridge_action"], "codex_card")
        self.assertEqual(value["session_key"], "feishu:p2p:c1")
        self.assertEqual(value["origin_message_id"], "m1")
        self.assertEqual(value["choice"], "a")

    def test_codex_reply_sends_card_block_and_strips_json(self):
        events = []
        final_replied = threading.Event()

        class FakeRunner:
            def run(self, prompt, thread_id):
                return (
                    "thread-card",
                    '请点卡片\n```feishu-card\n'
                    '{"config":{"wide_screen_mode":true},"elements":[{"tag":"action","actions":[{"tag":"button","text":{"tag":"plain_text","content":"确认"},"value":{"choice":"ok"}}]}]}\n'
                    '```',
                )

        with tempfile.TemporaryDirectory() as tmp:
            config = BridgeConfig("app", "secret", runtime_dir=Path(tmp), task_progress_seconds=0)
            bridge = FeishuCodexBridge(config, None, None, None)
            bridge.runner = FakeRunner()

            def fake_reply(message_id, text):
                events.append(("reply", text))

            def fake_card(message_id, card):
                events.append(("card", card))
                final_replied.set()
                return True

            bridge._reply_text = fake_reply
            bridge._reply_interactive = fake_card
            data = SimpleNamespace(
                event=SimpleNamespace(
                    message=SimpleNamespace(
                        message_id="m-card",
                        chat_id="c1",
                        chat_type="p2p",
                        message_type="text",
                        content=json.dumps({"text": "给我一个按钮"}, ensure_ascii=False),
                        root_id="",
                        parent_id="",
                        thread_id="",
                        create_time="",
                        mentions=None,
                    )
                )
            )

            bridge.handle_message(data)
            self.assertTrue(final_replied.wait(1))
            time.sleep(0.05)

        self.assertIn(("reply", "收到，我要开始干活了，稍等我"), events)
        self.assertIn(("reply", "请点卡片"), events)
        card_event = next(event for event in events if event[0] == "card")
        value = card_event[1]["elements"][0]["actions"][0]["value"]
        self.assertEqual(value["bridge_action"], "codex_card")
        self.assertEqual(value["session_key"], "feishu:p2p:c1")

    def test_codex_card_action_resumes_session(self):
        events = []
        final_replied = threading.Event()

        class FakeRunner:
            def run(self, prompt, thread_id):
                events.append(("run", thread_id, prompt))
                return "thread-existing", "已处理点击"

        with tempfile.TemporaryDirectory() as tmp:
            config = BridgeConfig("app", "secret", runtime_dir=Path(tmp), task_progress_seconds=0)
            bridge = FeishuCodexBridge(config, None, None, None)
            bridge.runner = FakeRunner()
            bridge.store.upsert_session("feishu:p2p:c1", "direct-chat")
            bridge.store.set_thread_id("feishu:p2p:c1", "thread-existing")

            def fake_reply(message_id, text):
                events.append(("reply", message_id, text))
                if text == "已处理点击":
                    final_replied.set()

            bridge._reply_text = fake_reply
            data = SimpleNamespace(
                event=SimpleNamespace(
                    chat_id="c1",
                    action=SimpleNamespace(
                        value={
                            "bridge_action": "codex_card",
                            "session_key": "feishu:p2p:c1",
                            "origin_message_id": "m-original",
                            "payload": {"choice": "ok"},
                        }
                    ),
                    operator=SimpleNamespace(open_id="ou_1", name="User"),
                )
            )

            bridge.handle_card_action(data)
            self.assertTrue(final_replied.wait(1))
            time.sleep(0.05)

        run_event = next(event for event in events if event[0] == "run")
        self.assertEqual(run_event[1], "thread-existing")
        self.assertIn('[card-click] {"choice": "ok"}', run_event[2])
        self.assertIn(("reply", "m-original", "已处理点击"), events)

    def test_codex_reply_creates_feishu_doc_block(self):
        events = []
        final_replied = threading.Event()

        class FakeRunner:
            def run(self, prompt, thread_id):
                return "thread-doc", '<feishu_doc title="复杂说明">这里是长说明</feishu_doc>\n一句摘要'

        class FakeDocClient:
            def create_document(self, request: FeishuDocRequest):
                events.append(("doc", request.title, request.content))
                return FeishuDocumentResult(request.title, "doc123", "https://feishu.cn/docx/doc123")

        with tempfile.TemporaryDirectory() as tmp:
            config = BridgeConfig("app", "secret", runtime_dir=Path(tmp), docs_enabled=True, task_progress_seconds=0)
            bridge = FeishuCodexBridge(config, None, None, None)
            bridge.runner = FakeRunner()
            bridge.doc_client = FakeDocClient()

            def fake_reply(message_id, text):
                events.append(("reply", text))
                if "https://feishu.cn/docx/doc123" in text:
                    final_replied.set()

            bridge._reply_text = fake_reply
            data = SimpleNamespace(
                event=SimpleNamespace(
                    message=SimpleNamespace(
                        message_id="m-doc",
                        chat_id="c1",
                        chat_type="p2p",
                        message_type="text",
                        content=json.dumps({"text": "复杂问题"}, ensure_ascii=False),
                        root_id="",
                        parent_id="",
                        thread_id="",
                        create_time="",
                        mentions=None,
                    )
                )
            )

            bridge.handle_message(data)
            self.assertTrue(final_replied.wait(1))
            time.sleep(0.05)

        self.assertIn(("doc", "复杂说明", "这里是长说明"), events)
        reply = next(event[1] for event in events if event[0] == "reply" and "doc123" in event[1])
        self.assertIn("一句摘要", reply)
        self.assertIn("已创建飞书文档：复杂说明", reply)


if __name__ == "__main__":
    unittest.main()
