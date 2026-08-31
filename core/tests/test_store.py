from app.store import Store


async def test_store_aller_retour(tmp_path):
    store = Store(tmp_path / "test.db")
    await store.open()
    try:
        await store.add_message("user", "Bonjour", source="voice")
        await store.add_message("assistant", "Salut Guillaume")

        messages = await store.recent_messages(10)
        assert [m["content"] for m in messages] == ["Bonjour", "Salut Guillaume"]
        assert messages[0]["source"] == "voice"
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

        # La limite garde bien les plus récents, en ordre chronologique
        derniers = await store.recent_messages(1)
        assert [m["content"] for m in derniers] == ["Salut Guillaume"]
    finally:
        await store.close()
