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


async def test_memoire_crud(tmp_path):
    store = Store(tmp_path / "mem.db")
    await store.open()
    try:
        a = await store.add_memory("Préfère les réponses courtes", category="preference")
        await store.add_memory("Habite à Lyon", category="fait", source="manuel")
        assert a["subject"] == "guillaume"  # sujet par défaut, prêt pour la Phase 2

        mems = await store.list_memories(subject="guillaume")
        assert [m["content"] for m in mems] == ["Préfère les réponses courtes", "Habite à Lyon"]
        assert mems[0]["source"] == "luna" and mems[1]["source"] == "manuel"

        # Mise à jour bornée aux champs autorisés
        updated = await store.update_memory(a["id"], content="Réponses très courtes", category="style")
        assert updated["content"] == "Réponses très courtes" and updated["category"] == "style"

        # Suppression
        assert await store.delete_memory(a["id"]) is True
        assert await store.delete_memory(a["id"]) is False  # déjà parti
        assert [m["content"] for m in await store.list_memories()] == ["Habite à Lyon"]
    finally:
        await store.close()


async def test_memoire_list_borne_aux_plus_recents(tmp_path):
    store = Store(tmp_path / "mem2.db")
    await store.open()
    try:
        for i in range(5):
            await store.add_memory(f"fait {i}")
        recents = await store.list_memories(limit=2)
        # Les 2 plus récents, en ordre chronologique
        assert [m["content"] for m in recents] == ["fait 3", "fait 4"]
    finally:
        await store.close()
