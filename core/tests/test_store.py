from datetime import datetime, timedelta, timezone

from app.store import Store


async def test_usage_metering(tmp_path):
    """Compteur de tokens : cumul par (jour, fournisseur, modèle), agrégation et
    filtre par date. Ne stocke que des nombres — jamais le contenu échangé."""
    store = Store(tmp_path / "usage.db")
    await store.open()
    try:
        await store.add_usage("claude", "claude-opus-5", 1000, 200)
        await store.add_usage("claude", "claude-opus-5", 500, 100)   # même seau → cumul
        await store.add_usage("openai", "gpt-4o", 4000, 800)

        rows = await store.usage_rows("2000-01-01")
        by = {(r["provider"], r["model"]): r for r in rows}
        claude = by[("claude", "claude-opus-5")]
        assert claude["turns"] == 2
        assert claude["input_tokens"] == 1500 and claude["output_tokens"] == 300
        assert by[("openai", "gpt-4o")]["input_tokens"] == 4000
        # Tri par volume de tokens décroissant (openai 4800 > claude 1800).
        assert rows[0]["provider"] == "openai"

        # Filtre : rien avant aujourd'hui n'est renvoyé si on démarre demain.
        tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        assert await store.usage_rows(tomorrow) == []

        # Entrées invalides ignorées sans lever.
        await store.add_usage("", "x", 1, 1)
        await store.add_usage("openai", "", 1, 1)
        assert len(await store.usage_rows("2000-01-01")) == 2
    finally:
        await store.close()


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


async def test_memoire_niveau_defaut_et_maj(tmp_path):
    """Brique 4 : chaque souvenir porte un niveau (défaut « utilisateur »)."""
    store = Store(tmp_path / "scope.db")
    await store.open()
    try:
        a = await store.add_memory("Aime le jazz")                       # défaut
        await store.add_memory("Cave à vin", scope="projet", category="fait")
        assert a["scope"] == "utilisateur"
        got = {m["content"]: m["scope"] for m in await store.list_memories()}
        assert got["Aime le jazz"] == "utilisateur" and got["Cave à vin"] == "projet"
        updated = await store.update_memory(a["id"], scope="maison")
        assert updated["scope"] == "maison"
    finally:
        await store.close()


async def test_memoire_migration_ajoute_le_niveau(tmp_path):
    """Une base d'avant la brique 4 (memories sans colonne scope) reçoit la colonne
    à l'ouverture ; les souvenirs existants passent au niveau « utilisateur »."""
    import aiosqlite

    db_path = tmp_path / "old.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE memories (id TEXT PRIMARY KEY, subject TEXT NOT NULL DEFAULT 'guillaume',"
            " category TEXT NOT NULL DEFAULT 'fait', content TEXT NOT NULL,"
            " source TEXT NOT NULL DEFAULT 'luna', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        await db.execute(
            "INSERT INTO memories (id, subject, category, content, source, created_at, updated_at)"
            " VALUES ('m1','guillaume','preference','Réponses courtes','luna','2026-01-01','2026-01-01')"
        )
        await db.commit()

    store = Store(db_path)
    await store.open()  # déclenche la migration idempotente
    try:
        mems = await store.list_memories(subject="guillaume")
        assert mems and mems[0]["scope"] == "utilisateur"      # legacy → profil stable
        await store.add_memory("Cuisine ouverte sur le salon", scope="maison")
        got = {m["content"]: m["scope"] for m in await store.list_memories()}
        assert got["Cuisine ouverte sur le salon"] == "maison"
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


async def test_pages_cycle_de_vie(tmp_path):
    store = Store(tmp_path / "pages.db")
    await store.open()
    try:
        p = await store.add_page(title="Mon Suivi", html="<h1>v1</h1>")
        assert p["slug"] == "mon-suivi"
        p2 = await store.add_page(title="Mon Suivi", html="x")
        assert p2["slug"] == "mon-suivi-2"  # slug rendu unique

        # Rien n'est en ligne avant publication
        assert await store.get_published_page("mon-suivi") is None
        assert all(not r["published"] for r in await store.list_pages())

        # Publication (action de Guillaume)
        await store.publish_page(p["id"])
        pub = await store.get_published_page("mon-suivi")
        assert pub and pub["published_html"] == "<h1>v1</h1>"
        rows = {r["id"]: r for r in await store.list_pages()}
        assert rows[p["id"]]["published"] is True and rows[p["id"]]["dirty"] is False

        # Éditer le brouillon ne touche PAS la version en ligne (marquée « dirty »)
        await store.update_page(p["id"], html="<h1>v2</h1>")
        rows = {r["id"]: r for r in await store.list_pages()}
        assert rows[p["id"]]["dirty"] is True
        assert (await store.get_published_page("mon-suivi"))["published_html"] == "<h1>v1</h1>"

        # Republier pousse la nouvelle version ; dépublier la retire
        await store.publish_page(p["id"])
        assert (await store.get_published_page("mon-suivi"))["published_html"] == "<h1>v2</h1>"
        await store.unpublish_page(p["id"])
        assert await store.get_published_page("mon-suivi") is None

        assert await store.delete_page(p["id"]) is True
    finally:
        await store.close()


async def test_suggestions_cycle_de_vie(tmp_path):
    store = Store(tmp_path / "sug.db")
    await store.open()
    try:
        s = await store.add_suggestion(
            kind="config", title="Monter l'effort de réflexion",
            rationale="Réponses plus travaillées le soir",
            target=".env.example",
            diff="--- a/.env.example\n+++ b/.env.example\n@@\n-SENTINEL_EFFORT=low\n+SENTINEL_EFFORT=medium\n",
        )
        assert s["status"] == "pending" and s["kind"] == "config"

        # La liste ne porte PAS le diff (métadonnées seulement) ; le get, oui.
        rows = await store.list_suggestions()
        assert rows[0]["id"] == s["id"] and "diff" not in rows[0]
        full = await store.get_suggestion(s["id"])
        assert "SENTINEL_EFFORT=medium" in full["diff"]

        # Décision humaine : acceptée → horodatée, filtrable par statut
        decided = await store.decide_suggestion(s["id"], "accepted")
        assert decided["status"] == "accepted" and decided["decided_at"]
        assert [r["id"] for r in await store.list_suggestions("accepted")] == [s["id"]]
        assert await store.list_suggestions("pending") == []

        assert await store.delete_suggestion(s["id"]) is True
        assert await store.get_suggestion(s["id"]) is None
    finally:
        await store.close()
