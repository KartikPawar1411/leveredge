from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent


def _run() -> AppTest:
    return AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=180).run()


def test_dashboard_renders_without_exceptions():
    at = _run()
    assert not at.exception, [e.value for e in at.exception]
    assert not any("cannot be run" in e.value for e in at.error)  # st.error is also used for bear-case flags
    labels = {m.label: m.value for m in at.metric}
    assert labels["Gross IRR"] == "18.1%"
    assert labels["MOIC"] == "2.30x"


def test_dashboard_reports_infeasible_inputs_instead_of_crashing():
    at = _run()
    lev = next(w for w in at.number_input if w.label.startswith("Senior term loan"))
    lev.set_value(12.0).run()
    assert not at.exception
    assert any("cannot be run" in e.value for e in at.error)

