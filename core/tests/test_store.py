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


async def test_memoire_par_sujet(tmp_path):
    store = Store(tmp_path / "mem3.db")
    await store.open()
    try:
        await store.add_memory("aime le jazz", subject="guillaume")
        await store.add_memory("préfère le thé", subject="camille")
        gui = await store.list_memories(subject="guillaume")
        cam = await store.list_memories(subject="camille")
        assert [m["content"] for m in gui] == ["aime le jazz"]
        assert [m["content"] for m in cam] == ["préfère le thé"]
    finally:
        await store.close()


async def test_profils_vocaux_crud(tmp_path):
    store = Store(tmp_path / "spk.db")
    await store.open()
    try:
        g = await store.add_speaker("Guillaume", is_owner=True)
        c = await store.add_speaker("Camille")
        await store.add_speaker_sample(g["id"], [1.0, 0.0, 0.0])
        await store.add_speaker_sample(g["id"], [0.9, 0.1, 0.0])
        await store.add_speaker_sample(c["id"], [0.0, 1.0, 0.0])

        # Liste avec compte d'empreintes, propriétaire en tête
        speakers = await store.list_speakers()
        assert speakers[0]["name"] == "Guillaume" and speakers[0]["is_owner"] == 1
        by_name = {s["name"]: s for s in speakers}
        assert by_name["Guillaume"]["samples"] == 2 and by_name["Camille"]["samples"] == 1

        # Profils prêts pour la reconnaissance : vecteurs regroupés
        profiles = {p["id"]: p for p in await store.speaker_profiles()}
        assert len(profiles[g["id"]]["vectors"]) == 2
        assert profiles[g["id"]]["is_owner"] is True

        # Suppression en cascade des empreintes
        assert await store.delete_speaker(g["id"]) is True
        remaining = await store.speaker_profiles()
        assert [p["id"] for p in remaining] == [c["id"]]
    finally:
        await store.close()
