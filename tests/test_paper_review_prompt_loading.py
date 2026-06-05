from types import SimpleNamespace

from apps.paper_review import api
from apps.paper_review.conference import load_profile
from apps.paper_review.prompts import load_pc_chair_prompt, load_role_prompt
from apps.paper_review.review import build_agent_configs


def test_hpdc_meta_synthesis_uses_meta_prompt_not_post_review_pc_chair_prompt():
    configs = build_agent_configs(load_profile("hpdc26"), "hpdc26")

    meta_prompt = configs["meta"].system_prompt
    assert "There is no separate PC Chair pass after you" in meta_prompt
    assert "interactive editor and advisor, not a second meta-reviewer" not in meta_prompt
    assert load_pc_chair_prompt("hpdc26") == load_role_prompt("hpdc26", "meta")


def test_hpdc_post_review_chat_uses_pc_chair_prompt():
    session = SimpleNamespace(config={"metadata": {"conference": "hpdc26"}})

    chat_prompt = api._pc_chair_system_prompt(session, "Review context.", "chair_editor")

    assert "interactive editor and advisor, not a second meta-reviewer" in chat_prompt
    assert "There is no separate PC Chair pass after you" not in chat_prompt
