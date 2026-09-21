"""Pure guidance helpers in bidlens.ui: risk badges and next-step selection."""

from bidlens import ui

HIGH = {"severity": "high", "code": "QUOTE_EXPIRED", "field": "valid_until", "message": "expired"}
MED = {"severity": "medium", "code": "FREIGHT_ESTIMATED", "field": "freight_cost", "message": "estimated"}
LOW = {"severity": "low", "code": "SUPPLIER_EXCEPTIONS", "field": "supplier_exceptions", "message": "note"}


def quote(status, flags=()):
    return {"status": status, "flags": list(flags), "filename": "q.pdf", "reviewed": None}


def test_risk_badge_and_level():
    assert ui.risk_badge([HIGH, HIGH, MED]) == "🔴 2 high · 🟠 1 medium"
    assert ui.risk_badge([HIGH, MED], compact=True) == "🔴1 🟠1"
    assert ui.risk_badge([LOW]) == "🟢 No risks flagged"  # info alone is not a risk
    assert ui.risk_level([MED, LOW]) == "medium"
    assert ui.risk_level([]) == "none"


def test_next_step_walks_the_workflow():
    assert ui.next_step([], None)[0] == "setup"
    page, label = ui.next_step([quote("extracted", [HIGH]), quote("approved"), quote("extracted")], None)
    assert page == "review" and label == "Next: review 2 quotes →"
    assert ui.next_step([quote("extracted"), quote("approved")], None)[1] == "Next: review 1 quote →"
    assert ui.next_step([quote("approved"), quote("rejected")], None)[0] == "setup"  # need 2 approved
    assert ui.next_step([quote("approved"), quote("approved")], None)[0] == "compare"
    assert ui.next_step([quote("approved"), quote("approved")], {"supplier": "X"})[0] == "scorecard"


def test_display_names():
    assert ui.short_name("JADEPORT FOUNDRY CO., LTD.") == "Jadeport Foundry"
    assert ui.short_name("Kessler & Vogt Gusstechnik GmbH") == "Kessler & Vogt Gusstechnik"
    assert ui.display_name("ABC Castings Inc.") == "ABC Castings Inc."  # mixed case left alone
    assert ui.short_name("Ironvale Components S.r.l.") == "Ironvale Components"
    assert ui.short_name("Kestrel Machining Pvt. Ltd.") == "Kestrel Machining"
    assert ui.short_name("Sierra Madre Castings S.A. de C.V.") == "Sierra Madre Castings"


def test_every_generator_suffix_is_trimmed():
    """The demo generator's suffix list and the display trimmer must not drift apart."""
    from bidlens.generate import COUNTRIES
    for code, (suffix, *_) in COUNTRIES.items():
        assert ui.short_name(f"Ironvale Castings {suffix}") == "Ironvale Castings", (code, suffix)


def test_common_words_are_not_mistaken_for_suffixes():
    assert ui.short_name("Heartland Ag") == "Heartland Ag"
    assert ui.short_name("Great Plains Ag Inc.") == "Great Plains Ag"
    assert ui.short_name("Nordic Pumps A/S") == "Nordic Pumps"
    assert ui.short_name("Delta Precision B.V.") == "Delta Precision"


def test_every_page_key_maps_to_a_real_page():
    from bidlens.config import ROOT
    for path in ui.PAGES.values():
        assert (ROOT / path).exists(), path


def test_scroll_to_top_is_one_shot(monkeypatch):
    """The scroll script renders only on the run right after a decision, then the flag is cleared."""
    from streamlit.testing.v1 import AppTest  # noqa: F401  (ensures streamlit test deps are present)
    import streamlit as st

    calls = []
    monkeypatch.setattr(ui.components, "html", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(st, "session_state", {})

    ui.scroll_to_top_if_requested()
    assert calls == []  # nothing requested

    ui.request_scroll_to_top()
    assert st.session_state["scroll_to_top"] is True
    ui.scroll_to_top_if_requested()
    assert len(calls) == 1 and "scrollTo" in calls[0][0]
    assert "scroll_to_top" not in st.session_state

    ui.scroll_to_top_if_requested()
    assert len(calls) == 1  # one-shot


def test_comparison_labels_are_unique_per_quote():
    rows = [{"quote_id": "a", "supplier": "Acme Inc.", "filename": "acme.pdf"},
            {"quote_id": "b", "supplier": "Acme Inc.", "filename": "acme_rev2.pdf"},
            {"quote_id": "c", "supplier": "Borealis GmbH", "filename": "b.pdf"}]
    assert ui.comparison_labels(rows) == {"a": "Acme Inc. (acme.pdf)", "b": "Acme Inc. (acme_rev2.pdf)",
                                          "c": "Borealis GmbH"}
    # Same supplier and same filename (different content): fall back to the quote id.
    rows[1]["filename"] = "acme.pdf"
    labels = ui.comparison_labels(rows)
    assert len(set(labels.values())) == 3 and labels["a"] == "Acme Inc. (acme.pdf) (a)"


# --- Navigation, branding and theme ---------------------------------------------------------------------
def test_navigation_lists_every_view_exactly_once():
    from bidlens.config import ROOT
    registered = [path for group in ui.NAV.values() for path, _, _ in group]
    on_disk = sorted(f"views/{p.name}" for p in (ROOT / "views").glob("*.py"))
    assert sorted(registered) == on_disk
    assert set(ui.PAGES.values()) <= set(registered)
    assert all(icon.startswith(":material/") for group in ui.NAV.values() for _, _, icon in group)


def test_views_are_not_in_a_pages_folder():
    """A `pages/` folder turns on Streamlit's legacy auto-navigation, which takes over the first run after every
    server start and shows file names instead of the sectioned menu built in Home.py."""
    from bidlens.config import ROOT
    assert not (ROOT / "pages").exists()


def test_theme_defines_light_and_dark_and_matches_the_chart_surfaces():
    import tomllib
    from bidlens.config import ROOT
    theme = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))["theme"]
    assert "base" not in theme  # a fixed base would stop the app following the viewer's light/dark setting
    for mode in ("light", "dark"):
        tokens = ui._CHART_TOKENS[mode]
        # The chart palette was validated against these surfaces; the segment gaps are drawn in the same colour.
        assert theme[mode]["backgroundColor"] == tokens["surface"]
        assert theme[mode]["chartCategoricalColors"][:5] == tokens["categorical"]
        assert (ROOT / "assets" / f"logo_{mode}.svg").exists()


def test_theme_falls_back_to_light_outside_a_browser_session():
    assert ui.theme_type() == "light" and ui.chart_tokens()["surface"] == "#fcfcfb"


def test_page_titles_use_material_icons_not_emoji():
    from bidlens.config import ROOT
    for path in (ROOT / "views").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "ui.page_header(" in source and "st.title(" not in source, path.name
