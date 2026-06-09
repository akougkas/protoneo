from types import SimpleNamespace

from apps.paper_review import api
from apps.paper_review.conference import load_profile
from apps.paper_review.prompts import load_pc_chair_prompt, load_role_prompt
from apps.paper_review.review import build_agent_configs


def test_adaptive_meta_synthesis_uses_meta_prompt_not_post_review_pc_chair_prompt():
    configs = build_agent_configs(load_profile("adaptive"), "adaptive")

    meta_prompt = configs["meta"].system_prompt
    assert "Synthesize the individual reviews" in meta_prompt
    assert "interactive editor and advisor" not in meta_prompt
    assert load_pc_chair_prompt("adaptive") != load_role_prompt("adaptive", "meta")


def test_adaptive_post_review_chat_uses_pc_chair_prompt():
    session = SimpleNamespace(config={"metadata": {"conference": "adaptive"}})

    chat_prompt = api._pc_chair_system_prompt(session, "Review context.", "chair_editor")

    assert "interactive editor and advisor" in chat_prompt
    assert "Synthesize the individual reviews" not in chat_prompt
