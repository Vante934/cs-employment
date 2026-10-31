# ruff: noqa: E402

"""Tests for Feishu welcome message functionality."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("lark_oapi")

from nanobot.bus.queue import MessageBus
from nanobot.channels.feishu.runtime import FeishuChannel, FeishuConfig


def _make_channel() -> FeishuChannel:
    config = FeishuConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        allow_from=["*"],
    )
    ch = FeishuChannel(config, MessageBus())
    ch._client = MagicMock()
    ch._running = True
    return ch


class TestWelcomeMessage:
    """Test welcome message functionality."""

    @pytest.mark.asyncio
    async def test_welcome_sent_to_new_user_in_dm(self):
        """Welcome message should be sent to new user in private chat."""
        ch = _make_channel()
        
        # Mock SQLite store
        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.session.sqlite_store import SqliteSessionStore
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._sqlite_store = store
            
            # Mock _send_message_sync
            ch._send_message_sync = MagicMock()
            
            # Check and send welcome
            await ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")
            
            # Should have sent welcome message
            ch._send_message_sync.assert_called_once()
            args = ch._send_message_sync.call_args[0]
            assert args[0] == "open_id"
            assert args[1] == "ou_user1"
            assert args[2] == "text"
            assert "小文" in args[3]
            
            # Should be marked as sent (use correct channel name)
            assert store.check_welcome_sent(ch.name, "ou_user1") is True

    @pytest.mark.asyncio
    async def test_welcome_not_sent_twice(self):
        """Welcome message should not be sent twice to same user."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.session.sqlite_store import SqliteSessionStore
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._sqlite_store = store
            
            # Mark as already sent (use correct channel name)
            store.mark_welcome_sent(ch.name, "ou_user1")
            
            # Mock _send_message_sync
            ch._send_message_sync = MagicMock()
            
            # Try to send welcome
            await ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")
            
            # Should NOT have sent
            ch._send_message_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_welcome_not_sent_in_group_chat(self):
        """Welcome message should not be sent in group chats."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.session.sqlite_store import SqliteSessionStore
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._sqlite_store = store
            
            # Mock _send_message_sync
            ch._send_message_sync = MagicMock()
            
            # Try to send welcome in group
            await ch._check_and_send_welcome("ou_user1", "oc_group1", "group")
            
            # Should NOT have sent
            ch._send_message_sync.assert_not_called()
            
            # Should NOT be marked as sent (use correct channel name)
            assert store.check_welcome_sent(ch.name, "ou_user1") is False

    @pytest.mark.asyncio
    async def test_welcome_failure_does_not_raise(self):
        """Welcome message failure should not raise exception."""
        ch = _make_channel()
        
        # Mock store to raise exception
        mock_store = MagicMock()
        mock_store.check_welcome_sent.side_effect = RuntimeError("DB error")
        ch._sqlite_store = mock_store
        
        # Should not raise
        await ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")

    @pytest.mark.asyncio
    async def test_welcome_marked_only_after_send_success(self):
        """Welcome should be marked as sent only after successful delivery."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.session.sqlite_store import SqliteSessionStore
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._sqlite_store = store
            
            # Mock _send_message_sync to fail
            ch._send_message_sync = MagicMock(side_effect=RuntimeError("Send failed"))
            
            # Try to send welcome (should fail)
            try:
                await ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")
            except Exception:
                pass
            
            # Should NOT be marked as sent (use correct channel name)
            assert store.check_welcome_sent(ch.name, "ou_user1") is False



class TestWelcomeMessageConcurrency:
    """Test welcome message concurrency safety."""

    @pytest.mark.asyncio
    async def test_concurrent_messages_same_user_single_welcome(self):
        """Two concurrent first messages from same user should send only one welcome."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.session.sqlite_store import SqliteSessionStore
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._sqlite_store = store

            # Track welcome sends
            send_count = 0

            def mock_send(*args):
                nonlocal send_count
                send_count += 1
                # First send: wait a bit to ensure second message arrives
                if send_count == 1:
                    import time
                    time.sleep(0.1)
                # Return message_id to indicate success
                return f"msg_{send_count}"

            ch._send_message_sync = MagicMock(side_effect=mock_send)

            # Send two concurrent messages
            task1 = asyncio.create_task(
                ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")
            )
            task2 = asyncio.create_task(
                ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")
            )

            await asyncio.gather(task1, task2)

            # Should only send once
            assert send_count == 1
            assert store.check_welcome_sent(ch.name, "ou_user1") is True

    @pytest.mark.asyncio
    async def test_concurrent_messages_different_users(self):
        """Concurrent first messages from different users should both send welcome."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.session.sqlite_store import SqliteSessionStore
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._sqlite_store = store

            # Track sends per user
            sends = []

            def mock_send(rid_type, user_id, msg_type, text):
                sends.append(user_id)
                # Return message_id to indicate success
                return f"msg_{user_id}"

            ch._send_message_sync = MagicMock(side_effect=mock_send)

            # Send concurrent messages from two different users
            task1 = asyncio.create_task(
                ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")
            )
            task2 = asyncio.create_task(
                ch._check_and_send_welcome("ou_user2", "ou_user2", "p2p")
            )

            await asyncio.gather(task1, task2)

            # Both should receive welcome
            assert len(sends) == 2
            assert "ou_user1" in sends
            assert "ou_user2" in sends
            assert store.check_welcome_sent(ch.name, "ou_user1") is True
            assert store.check_welcome_sent(ch.name, "ou_user2") is True

    @pytest.mark.asyncio
    async def test_many_concurrent_messages_same_user(self):
        """Many concurrent messages from same user should send only one welcome."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.session.sqlite_store import SqliteSessionStore
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._sqlite_store = store

            send_count = 0

            def mock_send(*args):
                nonlocal send_count
                send_count += 1
                # Simulate slow send
                import time
                time.sleep(0.05)
                # Return message_id to indicate success
                return f"msg_{send_count}"

            ch._send_message_sync = MagicMock(side_effect=mock_send)

            # Send 5 concurrent messages
            tasks = [
                asyncio.create_task(
                    ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")
                )
                for _ in range(5)
            ]

            await asyncio.gather(*tasks)

            # Should only send once
            assert send_count == 1
            assert store.check_welcome_sent(ch.name, "ou_user1") is True

    @pytest.mark.asyncio
    async def test_send_failure_allows_retry(self):
        """If welcome send fails, subsequent message should retry."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.session.sqlite_store import SqliteSessionStore
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._sqlite_store = store

            # First send fails
            ch._send_message_sync = MagicMock(side_effect=RuntimeError("Network error"))

            try:
                await ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")
            except Exception:
                pass

            # Should NOT be marked as sent
            assert store.check_welcome_sent(ch.name, "ou_user1") is False

            # Second attempt should succeed
            ch._send_message_sync = MagicMock()  # Succeeds now

            await ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")

            # Should be sent and marked
            ch._send_message_sync.assert_called_once()
            assert store.check_welcome_sent(ch.name, "ou_user1") is True

    @pytest.mark.asyncio
    async def test_concurrent_after_already_sent(self):
        """Concurrent messages after welcome already sent should not send again."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.session.sqlite_store import SqliteSessionStore
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._sqlite_store = store

            # Mark as already sent
            store.mark_welcome_sent(ch.name, "ou_user1")

            ch._send_message_sync = MagicMock()

            # Send multiple concurrent messages
            tasks = [
                asyncio.create_task(
                    ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")
                )
                for _ in range(3)
            ]

            await asyncio.gather(*tasks)

            # Should NOT send
            ch._send_message_sync.assert_not_called()


class TestWelcomeMessageContent:
    """Test welcome message content and JSON format."""

    @pytest.mark.asyncio
    async def test_welcome_message_json_format(self):
        """Welcome message content should be valid JSON."""
        import json
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.session.sqlite_store import SqliteSessionStore
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._sqlite_store = store

            sent_content = None

            def mock_send(rid_type, user_id, msg_type, content):
                nonlocal sent_content
                sent_content = content
                return "msg_123"

            ch._send_message_sync = MagicMock(side_effect=mock_send)

            await ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")

            # Verify content is valid JSON
            assert sent_content is not None
            parsed = json.loads(sent_content)
            assert "text" in parsed
            assert isinstance(parsed["text"], str)

    @pytest.mark.asyncio
    async def test_welcome_message_content_includes_capabilities(self):
        """Welcome message should include supported formats and limitations."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.session.sqlite_store import SqliteSessionStore
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._sqlite_store = store

            sent_content = None

            def mock_send(rid_type, user_id, msg_type, content):
                nonlocal sent_content
                sent_content = content
                return "msg_123"

            ch._send_message_sync = MagicMock(side_effect=mock_send)

            await ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")

            # Parse content
            import json
            parsed = json.loads(sent_content)
            text = parsed["text"]

            # Check for supported formats
            assert "PDF" in text
            assert "DOCX" in text
            assert "TXT" in text
            assert "Markdown" in text
            assert "XLSX" in text

            # Check for file size limit
            assert "50 MiB" in text or "50 MB" in text

            # Check for unsupported formats
            assert "PPTX" in text or "PPT" in text
            assert "DOC" in text

            # Check for image limitation
            assert "图片" in text or "识别" in text

    @pytest.mark.asyncio
    async def test_welcome_message_no_duplicate_json_encoding(self):
        """Content should not be double-encoded."""
        import json
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.session.sqlite_store import SqliteSessionStore
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._sqlite_store = store

            sent_content = None

            def mock_send(rid_type, user_id, msg_type, content):
                nonlocal sent_content
                sent_content = content
                return "msg_123"

            ch._send_message_sync = MagicMock(side_effect=mock_send)

            await ch._check_and_send_welcome("ou_user1", "ou_user1", "p2p")

            # Parse once should work
            parsed = json.loads(sent_content)
            text = parsed["text"]

            # Text should be plain string, not JSON-encoded again
            # If it were double-encoded, it would start with '"'
            assert not text.startswith('"')
            assert not text.endswith('"')