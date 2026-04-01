"""Tests for the Paper Review application layer."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from protoneo.agents.types import AgentOutput, Document
from protoneo.config.schema import AgentConfig
from protoneo.deliberation.types import DeliberationResult, PhaseResult
from apps.paper_review.conference import ConferenceProfile, load_profile, list_profiles
from apps.paper_review.prompts import (
    assemble_system_prompt,
    load_prompt_pack,
    load_role_prompt,
    load_shared_prompt,
)
from apps.paper_review.review import (
    _extract_json,
    build_agent_configs,
    build_deliberation_config,
    build_user_message,
    parse_meta_review,
    parse_review_output,
    result_to_packet,
)
from apps.paper_review.schemas import IndividualReview, MetaReview, ReviewPacket


# ── Conference Profile ─────────────────────────────────────

class TestConferenceProfile:
    def test_load_hpdc26(self):
        profile = load_profile("hpdc26")
        assert profile.slug == "hpdc26"
        assert "High-Performance" in profile.name
        assert profile.max_pages == 11
        assert profile.dual_anonymous is True
        assert len(profile.panel_agents) > 0

    def test_scope_text(self):
        profile = load_profile("hpdc26")
        text = profile.scope_text()
        assert "parallel" in text.lower() or "computing" in text.lower()

    def test_merit_labels(self):
        profile = load_profile("hpdc26")
        labels = profile.merit_labels()
        assert 1 in labels
        assert 5 in labels
        assert labels[1] == "Reject"
        assert labels[5] == "Strong accept"

    def test_expertise_labels(self):
        profile = load_profile("hpdc26")
        labels = profile.expertise_labels()
        assert 4 in labels
        assert labels[4] == "Expert"

    def test_panel_agents_defined(self):
        profile = load_profile("hpdc26")
        assert "technical" in profile.panel_agents
        assert "novelty" in profile.panel_agents
        assert "clarity" in profile.panel_agents
        assert "skeptic" in profile.panel_agents

    def test_list_profiles(self):
        profiles = list_profiles()
        assert len(profiles) >= 1
        slugs = [p.slug for p in profiles]
        assert "hpdc26" in slugs

    def test_load_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            load_profile("nonexistent_conf_99")

    def test_load_sc26(self):
        profile = load_profile("sc26")
        assert profile.slug == "sc26"
        assert "SC" in profile.short_name
        assert profile.max_pages == 12
        assert len(profile.panel_agents) >= 4


# ── Prompts ────────────────────────────────────────────────

class TestPrompts:
    def test_load_shared(self):
        shared = load_shared_prompt("hpdc26")
        assert len(shared) > 100
        assert "HPDC" in shared

    def test_load_role_technical(self):
        prompt = load_role_prompt("hpdc26", "technical")
        assert "Technical" in prompt
        assert "methodology" in prompt.lower()

    def test_load_role_novelty(self):
        prompt = load_role_prompt("hpdc26", "novelty")
        assert "Novelty" in prompt

    def test_load_role_clarity(self):
        prompt = load_role_prompt("hpdc26", "clarity")
        assert "Clarity" in prompt

    def test_load_role_skeptic(self):
        prompt = load_role_prompt("hpdc26", "skeptic")
        assert "Skeptic" in prompt

    def test_load_role_meta(self):
        prompt = load_role_prompt("hpdc26", "meta")
        assert "Meta-Reviewer" in prompt

    def test_assemble_includes_shared_and_overlay(self):
        prompt = assemble_system_prompt("hpdc26", "technical")
        assert "simulated" in prompt.lower()
        assert "methodology" in prompt.lower()

    def test_assemble_includes_conference_context(self):
        prompt = assemble_system_prompt(
            "hpdc26", "technical", conference_context="Test context data"
        )
        assert "Test context data" in prompt

    def test_load_prompt_pack(self):
        pack = load_prompt_pack("hpdc26")
        assert pack["conference"] == "hpdc26"
        assert "composition" in pack
        assert "technical" in pack["composition"]["roles"]

    def test_nonexistent_role_returns_empty(self):
        prompt = load_role_prompt("hpdc26", "nonexistent_role_xyz")
        assert prompt == ""


# ── Review Orchestration ───────────────────────────────────

class TestReviewOrchestration:
    def test_build_agent_configs(self):
        profile = load_profile("hpdc26")
        configs = build_agent_configs(profile, "hpdc26")
        assert "technical" in configs
        assert "novelty" in configs
        assert "clarity" in configs
        assert "skeptic" in configs
        assert "meta" in configs
        for key, cfg in configs.items():
            assert isinstance(cfg, AgentConfig)
            assert len(cfg.system_prompt) > 0
            assert len(cfg.model) > 0

    def test_build_agent_configs_with_overrides(self):
        profile = load_profile("hpdc26")
        overrides = {"technical": "openai/gpt-4o", "meta": "anthropic/claude-opus-4-6"}
        configs = build_agent_configs(profile, "hpdc26", model_map=overrides)
        assert configs["technical"].model == "openai/gpt-4o"
        assert configs["meta"].model == "anthropic/claude-opus-4-6"

    def test_build_agent_configs_with_artifact(self):
        profile = load_profile("hpdc26")
        configs = build_agent_configs(profile, "hpdc26", include_artifact=True)
        assert "artifact" in configs

    def test_meta_gets_more_tokens(self):
        profile = load_profile("hpdc26")
        configs = build_agent_configs(profile, "hpdc26")
        assert configs["meta"].max_tokens == 16384
        assert configs["technical"].max_tokens == 32768

    def test_build_deliberation_config(self):
        config = build_deliberation_config(max_rounds=2)
        assert config.pattern == "independent_synthesis"
        assert len(config.phases) == 3
        assert config.phases[0].mode == "parallel"
        assert config.phases[1].mode == "round_robin"
        assert config.phases[1].max_rounds == 2
        assert config.phases[2].mode == "sequential"

    def test_build_deliberation_config_custom_reviewers(self):
        config = build_deliberation_config(
            reviewer_ids=["technical", "systems", "novelty", "skeptic"],
            max_rounds=3,
        )
        assert config.phases[0].agents == ["technical", "systems", "novelty", "skeptic"]
        assert config.phases[1].agents == ["technical", "systems", "novelty", "skeptic"]
        assert config.phases[1].max_rounds == 3
        assert config.phases[2].agents == ["meta"]

    def test_sc26_agents_are_profile_driven(self):
        """SC26 defines 'systems' instead of 'clarity'. Agent configs must reflect this."""
        profile = load_profile("sc26")
        configs = build_agent_configs(profile, "sc26")
        assert "technical" in configs
        assert "systems" in configs
        assert "novelty" in configs
        assert "skeptic" in configs
        assert "meta" in configs
        assert "clarity" not in configs  # SC26 does not have a clarity reviewer

    def test_sc26_systems_reviewer_has_prompt(self):
        profile = load_profile("sc26")
        configs = build_agent_configs(profile, "sc26")
        assert len(configs["systems"].system_prompt) > 100
        assert "Systems" in configs["systems"].role

    def test_build_user_message(self):
        profile = load_profile("hpdc26")
        doc = Document(
            document_id="d1",
            filename="test.pdf",
            text="This is a test paper about GPU optimization.",
        )
        msg = build_user_message(doc, profile)
        assert "HPDC" in msg
        assert "MANUSCRIPT" in msg
        assert "GPU optimization" in msg


# ── JSON Extraction ────────────────────────────────────────

class TestJsonExtraction:
    def test_raw_json(self):
        text = '{"reviewer_role": "Technical", "summary": "Good paper"}'
        result = _extract_json(text)
        assert result is not None
        assert result["reviewer_role"] == "Technical"

    def test_fenced_json(self):
        text = 'Here is my review:\n```json\n{"score": 4}\n```\nDone.'
        result = _extract_json(text)
        assert result is not None
        assert result["score"] == 4

    def test_embedded_json(self):
        text = 'Some preamble text {"key": "value"} trailing text'
        result = _extract_json(text)
        assert result is not None
        assert result["key"] == "value"

    def test_no_json(self):
        text = "This is plain text with no JSON."
        result = _extract_json(text)
        assert result is None


# ── Output Parsing ─────────────────────────────────────────

class TestOutputParsing:
    def _make_output(self, content: str, agent_id: str = "tech_1", role: str = "Technical"):
        return AgentOutput(
            agent_id=agent_id,
            agent_role=role,
            content=content,
            metadata={"model": "test/model"},
        )

    def test_parse_structured_review(self):
        content = json.dumps({
            "reviewer_role": "Technical Depth Reviewer",
            "summary": "A solid systems paper.",
            "overall_merit": {"score": 4, "label": "Accept"},
            "strengths": [{"point": "Good methodology", "evidence": "Section 4"}],
            "weaknesses": [{"point": "Missing ablation", "severity": "high"}],
        })
        output = self._make_output(content)
        review = parse_review_output(output, "technical")
        assert review.reviewer_role == "technical"
        assert review.summary == "A solid systems paper."
        assert review.overall_merit["score"] == 4
        assert len(review.strengths) == 1

    def test_parse_unstructured_falls_back(self):
        output = self._make_output("This paper is interesting but has flaws.")
        review = parse_review_output(output, "novelty")
        assert review.reviewer_role == "novelty"
        assert "interesting" in review.comments_for_authors
        assert review.raw_content == output.content

    def test_parse_meta_review_structured(self):
        content = json.dumps({
            "panel_summary": "Mixed reviews.",
            "final_recommendation": {"score": 3, "label": "Weak accept"},
            "consensus": {"level": "moderate"},
            "decision_risk_notes": ["Baseline fairness concern"],
        })
        output = self._make_output(content, agent_id="meta_1", role="Meta-Reviewer")
        meta = parse_meta_review(output)
        assert meta.panel_summary == "Mixed reviews."
        assert meta.final_recommendation["score"] == 3
        assert len(meta.decision_risk_notes) == 1

    def test_parse_meta_review_unstructured(self):
        output = self._make_output(
            "Overall the paper is borderline.", agent_id="meta_1"
        )
        meta = parse_meta_review(output)
        assert "borderline" in meta.author_facing_summary


# ── Result to Packet ───────────────────────────────────────

class TestResultToPacket:
    def test_converts_full_result(self):
        profile = load_profile("hpdc26")

        review_content = json.dumps({
            "reviewer_role": "Technical",
            "summary": "Good paper.",
            "overall_merit": {"score": 4},
        })
        meta_content = json.dumps({
            "panel_summary": "Consensus accept.",
            "final_recommendation": {"score": 4, "label": "Accept"},
        })

        result = DeliberationResult(
            session_id="test-session",
            phases=[
                PhaseResult(
                    phase_name="independent_review",
                    mode="parallel",
                    outputs=[
                        AgentOutput(
                            agent_id="technical_1",
                            agent_role="Technical",
                            content=review_content,
                            metadata={"model": "test"},
                        ),
                    ],
                ),
                PhaseResult(
                    phase_name="deliberation",
                    mode="round_robin",
                    outputs=[
                        AgentOutput(
                            agent_id="technical_1",
                            agent_role="Technical",
                            content="I stand by my assessment.",
                            metadata={"model": "test", "round": 1},
                        ),
                    ],
                ),
                PhaseResult(
                    phase_name="meta_review",
                    mode="sequential",
                    outputs=[
                        AgentOutput(
                            agent_id="meta_1",
                            agent_role="Meta-Reviewer",
                            content=meta_content,
                            metadata={"model": "test"},
                        ),
                    ],
                ),
            ],
            duration_seconds=42.5,
            total_cost=0.15,
        )

        packet = result_to_packet(result, profile, paper_title="Test Paper")
        assert packet.session_id == "test-session"
        assert packet.conference == "hpdc26"
        assert packet.paper_title == "Test Paper"
        assert len(packet.reviews) == 1
        assert packet.reviews[0].overall_merit["score"] == 4
        assert len(packet.deliberation) == 1
        assert packet.meta_review.panel_summary == "Consensus accept."
        assert packet.duration_seconds == 42.5


# ── Preflight ────────────────────────────────────────────────

class TestPreflight:
    def setup_method(self):
        self.profile = load_profile("hpdc26")

    def test_page_estimate_within_limit(self):
        from apps.paper_review.preflight import run_preflight
        text = "x" * 50000  # ~8 pages
        result = run_preflight(text, "test.pdf", self.profile)
        page_check = next(c for c in result.checks if c.name == "page_limit")
        assert page_check.passed

    def test_page_estimate_over_limit(self):
        from apps.paper_review.preflight import run_preflight
        text = "x" * 100000  # ~16 pages
        result = run_preflight(text, "test.pdf", self.profile)
        page_check = next(c for c in result.checks if c.name == "page_limit")
        assert not page_check.passed

    def test_section_detection(self):
        from apps.paper_review.preflight import run_preflight
        text = (
            "Abstract\nThis paper presents...\n"
            "Introduction\nWe propose...\n"
            "Related Work\nPrior studies...\n"
            "Methodology\nOur approach...\n"
            "Evaluation\nWe tested...\n"
            "Conclusion\nIn summary...\n"
            "References\n[1] Foo et al.\n"
        )
        result = run_preflight(text, "test.pdf", self.profile)
        section_check = next(c for c in result.checks if c.name == "sections")
        assert section_check.passed

    def test_anonymization_flags_identity_leak(self):
        from apps.paper_review.preflight import run_preflight
        text = "As we showed in our previous work [5], the system can..."
        result = run_preflight(text, "test.pdf", self.profile)
        anon_check = next(c for c in result.checks if c.name == "anonymization")
        assert not anon_check.passed

    def test_anonymization_passes_clean_text(self):
        from apps.paper_review.preflight import run_preflight
        text = "The authors propose a novel approach to distributed computing."
        result = run_preflight(text, "test.pdf", self.profile)
        anon_check = next(c for c in result.checks if c.name == "anonymization")
        assert anon_check.passed

    def test_venue_fit_hpc_paper(self):
        from apps.paper_review.preflight import run_preflight
        text = "GPU parallel distributed HPC cluster MPI CUDA scheduling scalability benchmark"
        result = run_preflight(text, "test.pdf", self.profile)
        fit_check = next(c for c in result.checks if c.name == "venue_fit")
        assert fit_check.passed

    def test_venue_fit_unrelated_paper(self):
        from apps.paper_review.preflight import run_preflight
        text = "quantum biology protein folding enzyme catalysis molecular dynamics"
        result = run_preflight(text, "test.pdf", self.profile)
        fit_check = next(c for c in result.checks if c.name == "venue_fit")
        assert not fit_check.passed

    def test_can_proceed_no_blockers(self):
        from apps.paper_review.preflight import run_preflight
        text = "Abstract\nIntroduction\nMethodology\nEvaluation\nConclusion\nReferences\n" * 10
        result = run_preflight(text, "test.pdf", self.profile)
        assert result.block_count == 0

    def test_limitations_detected(self):
        from apps.paper_review.preflight import run_preflight
        text = "Limitations\nThis study has several limitations..."
        result = run_preflight(text, "test.pdf", self.profile)
        lim_check = next(c for c in result.checks if c.name == "limitations")
        assert lim_check.passed

    def test_references_count(self):
        from apps.paper_review.preflight import run_preflight
        refs = " ".join(f"[{i}]" for i in range(1, 30))
        result = run_preflight(refs, "test.pdf", self.profile)
        ref_check = next(c for c in result.checks if c.name == "references")
        assert ref_check.passed


# ── Export ────────────────────────────────────────────────

class TestMarkdownExport:
    def test_renders_header(self):
        from apps.paper_review.export import packet_to_markdown
        from apps.paper_review.schemas import ReviewPacket
        packet = ReviewPacket(
            session_id="abc123",
            conference="hpdc26",
            paper_title="Test Paper",
            duration_seconds=42.5,
        )
        md = packet_to_markdown(packet)
        assert "# Review Packet: Test Paper" in md
        assert "HPDC26" in md
        assert "abc123" in md

    def test_renders_reviews(self):
        from apps.paper_review.export import packet_to_markdown
        from apps.paper_review.schemas import IndividualReview, ReviewPacket
        packet = ReviewPacket(
            session_id="abc",
            conference="hpdc26",
            reviews=[
                IndividualReview(
                    reviewer_role="Technical Reviewer",
                    agent_id="tech1",
                    model="test-model",
                    summary="Good paper",
                    overall_merit={"score": 4, "label": "Accept"},
                    strengths=[{"point": "Strong methodology", "evidence": "Section 4"}],
                    weaknesses=[{"point": "Missing baselines", "severity": "high"}],
                    questions_for_authors=["Why not use X?"],
                ),
            ],
        )
        md = packet_to_markdown(packet)
        assert "### Technical Reviewer" in md
        assert "Strong methodology" in md
        assert "Missing baselines" in md
        assert "Why not use X?" in md

    def test_renders_meta_review(self):
        from apps.paper_review.export import packet_to_markdown
        from apps.paper_review.schemas import MetaReview, ReviewPacket
        packet = ReviewPacket(
            session_id="abc",
            conference="hpdc26",
            meta_review=MetaReview(
                panel_summary="Reviewers agree this is a strong paper.",
                final_recommendation={"score": 4, "label": "Accept"},
                consensus={"level": "Strong"},
                agreements=["Solid methodology"],
                disagreements=[{"issue": "Novelty", "your_resolution": "Sufficient"}],
            ),
        )
        md = packet_to_markdown(packet)
        assert "## Panel Summary" in md
        assert "strong paper" in md
        assert "Final Recommendation" in md
        assert "Solid methodology" in md


# ── Paper Metadata Extraction ─────────────────────────────

class TestDocumentMetadata:
    """Tests for heuristic metadata extraction from academic paper text."""

    SAMPLE_PAPER = (
        "Accelerating Distributed Training with Adaptive Gradient Compression\n"
        "\n"
        "Jane Doe, John Smith\n"
        "Department of Computer Science\n"
        "\n"
        "Abstract\n"
        "We present a novel approach to gradient compression that reduces\n"
        "communication overhead in distributed deep learning training by 4x\n"
        "while maintaining convergence guarantees.\n"
        "\n"
        "1. Introduction\n"
        "Distributed training of deep neural networks requires frequent\n"
        "gradient synchronization across workers.\n"
        "\n"
        "2. Related Work\n"
        "Prior work on gradient compression includes Top-K sparsification.\n"
        "\n"
        "3. Methodology\n"
        "Our approach combines adaptive thresholding with error feedback.\n"
        "Figure 1 shows the architecture. Figure 2 shows the compression ratio.\n"
        "Table 1 summarizes the hyperparameters. Table 2 lists the benchmarks.\n"
        "Table 3 compares memory usage.\n"
        "\n"
        "4. Evaluation\n"
        "We evaluate on ImageNet and CIFAR-10. Figure 3 shows convergence.\n"
        "\n"
        "5. Conclusion\n"
        "We demonstrated 4x speedup with minimal accuracy loss.\n"
        "\n"
        "References\n"
        "[1] Dean et al. Large scale distributed deep networks. NIPS 2012.\n"
        "[2] Alistarh et al. QSGD: Communication-efficient SGD. NIPS 2017.\n"
        "[3] Lin et al. Deep gradient compression. ICLR 2018.\n"
        "[4] Stich et al. Sparsified SGD with memory. NeurIPS 2018.\n"
    )

    def test_extract_title(self):
        from protoneo.knowledge.metadata import extract_metadata
        meta = extract_metadata(self.SAMPLE_PAPER)
        assert "Adaptive Gradient Compression" in meta.title

    def test_extract_abstract(self):
        from protoneo.knowledge.metadata import extract_metadata
        meta = extract_metadata(self.SAMPLE_PAPER)
        assert "gradient compression" in meta.abstract.lower()
        assert "4x" in meta.abstract

    def test_extract_sections(self):
        from protoneo.knowledge.metadata import extract_metadata
        meta = extract_metadata(self.SAMPLE_PAPER)
        headings_lower = [s.lower() for s in meta.sections]
        assert any("introduction" in h for h in headings_lower)
        assert any("methodology" in h for h in headings_lower)
        assert any("evaluation" in h for h in headings_lower)
        assert any("conclusion" in h for h in headings_lower)

    def test_figure_count(self):
        from protoneo.knowledge.metadata import extract_metadata
        meta = extract_metadata(self.SAMPLE_PAPER)
        assert meta.figure_count == 3

    def test_table_count(self):
        from protoneo.knowledge.metadata import extract_metadata
        meta = extract_metadata(self.SAMPLE_PAPER)
        assert meta.table_count == 3

    def test_reference_count(self):
        from protoneo.knowledge.metadata import extract_metadata
        meta = extract_metadata(self.SAMPLE_PAPER)
        assert meta.reference_count == 4

    def test_references_list(self):
        from protoneo.knowledge.metadata import extract_metadata
        meta = extract_metadata(self.SAMPLE_PAPER)
        assert len(meta.references) == 4
        assert "Dean" in meta.references[0]

    def test_word_count(self):
        from protoneo.knowledge.metadata import extract_metadata
        meta = extract_metadata(self.SAMPLE_PAPER)
        assert meta.estimated_word_count > 50

    def test_empty_text(self):
        from protoneo.knowledge.metadata import extract_metadata
        meta = extract_metadata("")
        assert meta.title == ""
        assert meta.abstract == ""
        assert meta.sections == []
        assert meta.figure_count == 0
        assert meta.reference_count == 0

    def test_preflight_includes_metadata(self):
        """Preflight result now includes paper metadata."""
        from apps.paper_review.preflight import run_preflight
        profile = load_profile("hpdc26")
        result = run_preflight(self.SAMPLE_PAPER, "test.pdf", profile)
        assert result.metadata is not None
        assert "Gradient Compression" in result.metadata.title
        assert result.metadata.figure_count == 3

    def test_metadata_with_roman_numeral_sections(self):
        from protoneo.knowledge.metadata import extract_metadata
        text = (
            "A Study of Cache Performance\n\n"
            "Abstract\nWe study cache behavior.\n\n"
            "I. Introduction\nCaches are important.\n\n"
            "II. Background\nPrior work exists.\n\n"
            "III. Design\nWe propose a new cache.\n\n"
            "IV. Results\nFigure 1 shows speedup.\n\n"
            "V. Conclusion\nWe improved caches.\n\n"
        )
        meta = extract_metadata(text)
        assert len(meta.sections) >= 4


# ── PDF Export ─────────────────────────────────────────────

class TestPdfExport:
    def test_generates_pdf_bytes(self):
        from apps.paper_review.export import packet_to_pdf
        from apps.paper_review.schemas import ReviewPacket, IndividualReview
        packet = ReviewPacket(
            session_id="pdf-test",
            conference="hpdc26",
            paper_title="Test PDF Export",
            duration_seconds=120,
            reviews=[
                IndividualReview(
                    reviewer_role="Technical",
                    agent_id="tech1",
                    model="test-model",
                    summary="Good paper with strong results.",
                    strengths=[{"point": "Solid experiments"}],
                    weaknesses=[{"point": "Missing related work", "severity": "medium"}],
                ),
            ],
        )
        pdf = packet_to_pdf(packet)
        assert isinstance(pdf, bytes)
        assert len(pdf) > 100
        # PDF magic bytes
        assert pdf[:5] == b"%PDF-"


# ── Review Checkpoints ────────────────────────────────────────

class TestReviewCheckpoints:
    def test_write_review_checkpoint(self):
        """C4: _write_review_checkpoint appends a StageCheckpoint."""
        from protoneo.deliberation.session import Session
        from apps.paper_review.pipeline import _write_review_checkpoint

        session = Session()
        assert len(session.checkpoints) == 0

        _write_review_checkpoint(session, "independent_review")
        assert len(session.checkpoints) == 1
        assert session.checkpoints[0].stage_name == "independent_review"
        assert session.last_checkpoint == "independent_review"

        # Writing same checkpoint again is idempotent
        _write_review_checkpoint(session, "independent_review")
        assert len(session.checkpoints) == 1

    def test_all_review_stages_get_checkpoints(self):
        """C4: all four review stages can be checkpointed."""
        from protoneo.deliberation.session import Session
        from apps.paper_review.pipeline import _write_review_checkpoint

        session = Session()
        for stage in ["independent_review", "deliberation", "meta_review", "pc_chair"]:
            _write_review_checkpoint(session, stage)

        assert len(session.checkpoints) == 4
        names = [cp.stage_name for cp in session.checkpoints]
        assert names == ["independent_review", "deliberation", "meta_review", "pc_chair"]
