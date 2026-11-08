import pytest

pytestmark = pytest.mark.integration


async def test_add_and_vector_search(runtime):
    m = runtime.memory
    await m.add("Acme Roofing is a roofing contractor in Austin Texas", metadata={"src": "t"})
    await m.add("Chocolate cake recipe with strawberries")
    await m.add("Dallas Texas roofing contractor list for outreach")
    await m.add("Roofing note in another namespace", namespace="other")
    hits = await m.search("roofing contractor Texas", limit=3)
    assert len(hits) == 3
    assert "cake" in hits[-1].content
    assert hits[0].score >= hits[1].score >= hits[2].score
    assert all(h.namespace == "default" for h in hits)
    assert hits[0].metadata == {"src": "t"} or hits[1].metadata == {"src": "t"}
    assert await m.delete(hits[0].id) and not await m.delete(hits[0].id)


async def test_memory_skills_with_real_store(runtime):
    ctx = runtime.skill_context(agent_name="t")
    r = await runtime.skills.invoke("memory.remember", {"content": "Client prefers email"}, ctx)
    assert r.ok
    r = await runtime.skills.invoke(
        "memory.recall", {"query": "how does client prefer contact"}, ctx
    )
    assert r.ok and r.output["hits"][0]["content"] == "Client prefers email"
