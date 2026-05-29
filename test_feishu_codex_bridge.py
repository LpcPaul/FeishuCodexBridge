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
    FeishuCodexBridge,
    MessageEnvelope,
    MOBILE_REPLY_CONTEXT,
    StateStore,
    build_prompt,
    extract_message_text,
    parse_last_agent_message,
    parse_thread_id,
    route_message,
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

    def test_direct_chat_starts_new_topic_after_idle_timeout(self):
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
            self.assertEqual(later.route.session_key, "feishu:p2p:c1:topic:2")
            self.assertIsNotNone(later.notice)
            self.assertEqual(later.notice.previous_session_key, "feishu:p2p:c1")

    def test_restore_previous_topic_switches_active_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.sqlite")
            route = route_message(MessageEnvelope("m1", "c1", "p2p", "text", "第一件事", "", "", "", 1_000, True))
            store.resolve_topic(route, MessageEnvelope("m1", "c1", "p2p", "text", "第一件事", "", "", "", 1_000, True), 7200)
            store.resolve_topic(route, MessageEnvelope("m2", "c1", "p2p", "text", "另一件事", "", "", "", 7_201_001, True), 7200)

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

            completed_ms = 8 * 60 * 60 * 1000
            store.finish_task("feishu:p2p:c1", completed_ms=completed_ms)
            one_hour_later = store.resolve_topic(
                route,
                MessageEnvelope("m3", "c1", "p2p", "text", "继续", "", "", "", completed_ms + 3_600_000, True),
                7200,
            )
            self.assertEqual(one_hour_later.route.session_key, "feishu:p2p:c1")

            three_hours_later = store.resolve_topic(
                route,
                MessageEnvelope("m4", "c1", "p2p", "text", "新事", "", "", "", completed_ms + 10_800_001, True),
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

    def test_handle_message_sends_topic_notice_card_after_idle_timeout(self):
        events = []
        runner_started = threading.Event()
        final_replied = threading.Event()

        class FakeRunner:
            def run(self, prompt, thread_id):
                events.append(("run", thread_id, "auto-new-topic" in prompt))
                runner_started.set()
                return "thread-2", "新话题回复"

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
                if text == "新话题回复":
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

        self.assertEqual(events[0], ("card", "已开启新话题"))
        self.assertNotIn(("reply", "收到，我要开始干活了，稍等我"), events)
        self.assertIn(("run", None, True), events)
        self.assertIn(("reply", "新话题回复"), events)

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
            bridge.store.resolve_topic(route, MessageEnvelope("m2", "c1", "p2p", "text", "新话题", "", "", "", 7_201_001, True), 7200)

            data = SimpleNamespace(
                event=SimpleNamespace(
                    action=SimpleNamespace(
                        value={
                            "bridge_action": "topic_boundary",
                            "choice": "continue_previous",
                            "base_session_key": "feishu:p2p:c1",
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


if __name__ == "__main__":
    unittest.main()
