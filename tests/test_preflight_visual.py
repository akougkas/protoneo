from apps.paper_review.conference import ConferenceProfile, load_profile
from apps.paper_review.preflight import run_preflight


def _has_sc26() -> bool:
    try:
        load_profile("sc26")
        return True
    except FileNotFoundError:
        return False


def _minimal_profile() -> ConferenceProfile:
    return ConferenceProfile(
        slug="test",
        name="TestConf",
        short_name="TEST",
        max_pages=12,
        dual_anonymous=False,
    )


def test_preflight_visual_check_warns_when_vlm_unreachable():
    profile = load_profile("sc26") if _has_sc26() else _minimal_profile()
    result = run_preflight(
        "Abstract ... parallel GPU MPI evaluation results conclusion references [1][2]",
        "p.pdf",
        profile,
        figure_count=7,
        table_count=3,
        vlm_status={
            "configured": True,
            "reachable": False,
            "model": "omni",
            "error": "timeout",
        },
    )
    check = [c for c in result.checks if c.name == "visual_evidence"][0]
    assert check.severity == "warning"
    assert "7 figures" in check.detail


def test_preflight_visual_check_info_when_vision_ready():
    result = run_preflight(
        "parallel gpu mpi evaluation",
        "p.pdf",
        _minimal_profile(),
        figure_count=4,
        table_count=1,
        vlm_status={
            "configured": True,
            "reachable": True,
            "model": "omni",
            "error": "",
        },
    )
    check = [c for c in result.checks if c.name == "visual_evidence"][0]
    assert check.passed and check.severity == "info"
    assert "vision-grounded" in check.detail.lower()
