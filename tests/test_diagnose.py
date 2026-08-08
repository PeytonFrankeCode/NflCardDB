

def test_signed_out_leads_the_verdict_when_the_search_itself_works():
    """The real diagnosis on a report whose method checks all failed: the
    searches went through, the session did not exist."""
    from nflcarddb.diagnose import (
        CHALLENGED, REFUSED, WORKING, Diagnosis, EngineCheck, StageCheck,
        format_report,
    )

    report = format_report(Diagnosis(
        checks=[
            EngineCheck("requests", REFUSED, 403),
            EngineCheck("impersonate", REFUSED, 403),
            EngineCheck("browser", CHALLENGED, 200),
        ],
        stages=[
            StageCheck("homepage", "u", 200, CHALLENGED, "challenge", 0),
            StageCheck("plain search", "u", 200, WORKING, "parsed 53", 53),
            StageCheck("sold search", "u", 200, WORKING, "parsed 56", 56),
        ],
        signed_in=False,
    ))

    assert "SIGNED OUT" in report
    assert "login.bat" in report
    # The old verdict said this, and it was simply untrue.
    assert "Nothing got through" not in report


def test_a_working_staged_search_is_not_reported_as_total_failure():
    """Cold one-off requests get challenged where a warmed session succeeds;
    the collector uses the warmed path, so that is not 'nothing works'."""
    from nflcarddb.diagnose import (
        CHALLENGED, WORKING, Diagnosis, EngineCheck, StageCheck, format_report,
    )

    report = format_report(Diagnosis(
        checks=[EngineCheck("browser", CHALLENGED, 200)],
        stages=[StageCheck("sold search", "u", 200, WORKING, "parsed 56", 56)],
        signed_in=True,
    ))

    assert "Nothing got through" not in report
    assert "56 listings" in report


def test_genuine_total_failure_still_says_so():
    from nflcarddb.diagnose import (
        REFUSED, Diagnosis, EngineCheck, StageCheck, format_report,
    )

    report = format_report(Diagnosis(
        checks=[EngineCheck("requests", REFUSED, 403)],
        stages=[StageCheck("sold search", "u", 403, REFUSED, "HTTP 403", 0)],
        signed_in=False,
    ))

    assert "Nothing got through" in report
