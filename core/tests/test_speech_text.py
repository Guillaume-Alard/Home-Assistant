from app.brain.speech_text import SentenceChunker, markdown_to_speech


def test_chunker_decoupe_les_phrases_completes():
    c = SentenceChunker()
    assert c.feed("Bonjour. Comment vas-tu ? Je") == ["Bonjour.", "Comment vas-tu ?"]
    assert c.feed(" vais bien.") == []
    assert c.flush() == "Je vais bien."
    assert c.flush() is None


def test_chunker_ignore_les_nombres_decimaux():
    c = SentenceChunker()
    out = c.feed("Il fait 21.5 degrés dehors. Oui")
    assert out == ["Il fait 21.5 degrés dehors."]
    assert c.flush() == "Oui"


def test_chunker_coupe_aux_sauts_de_ligne():
    c = SentenceChunker()
    assert c.feed("Premier point\nDeuxième point") == ["Premier point"]
    assert c.flush() == "Deuxième point"


def test_chunker_garde_les_blocs_de_code_entiers():
    c = SentenceChunker()
    out = []
    for delta in ["Voici :\n```python\npri", "nt('a. b')\n```\nVoilà. Fin"]:
        out.extend(c.feed(delta))
    out_flush = c.flush()
    assert out[0] == "Voici :"
    assert out[1] == "```python\nprint('a. b')\n```"  # le point interne n'a pas coupé
    assert out[2] == "Voilà."
    assert out_flush == "Fin"


def test_chunker_coupe_une_tres_longue_phrase_sans_ponctuation():
    c = SentenceChunker(max_buffer=50)
    out = c.feed("mot " * 30)
    assert out, "une coupure de sécurité doit se produire"
    assert all(len(s) <= 60 for s in out)


def test_markdown_to_speech_nettoie_le_style():
    assert (
        markdown_to_speech("**Important :** lance `docker ps` puis lis [la doc](https://exemple.fr).")
        == "Important : lance docker ps puis lis la doc."
    )


def test_markdown_to_speech_remplace_le_code():
    out = markdown_to_speech("Voici :\n```python\nprint('x')\n```")
    assert "print" not in out
    assert "code affiché à l'écran" in out


def test_markdown_to_speech_supprime_les_tableaux():
    assert markdown_to_speech("| a | b |\n| - | - |") == ""
