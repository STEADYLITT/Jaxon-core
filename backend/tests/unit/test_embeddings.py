import math

from jaxon.memory.embeddings import HashingEmbedder, cosine


def test_deterministic_and_normalized():
    e = HashingEmbedder(384)
    a, b = e.embed_one("Hello world"), e.embed_one("hello   WORLD")
    assert a == b and len(a) == 384
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-9)
    assert e.embed_one("") == [0.0] * 384


def test_similarity_ranks_related_text_higher():
    e = HashingEmbedder(384)
    q = e.embed_one("roofing contractor leads in Texas")
    related = e.embed_one("list of roofing contractor companies in Texas")
    unrelated = e.embed_one("chocolate cake recipe with strawberries")
    assert cosine(q, related) > cosine(q, unrelated)
