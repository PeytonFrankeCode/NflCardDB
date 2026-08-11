

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


def _stage(name, outcome, n=0):
    from nflcarddb.diagnose import StageCheck
    return StageCheck(name, "u", 200, outcome, "d", n)


def test_bisect_names_the_first_parameter_that_fails():
    """The whole point: which rung broke, not merely that something did."""
    from nflcarddb.diagnose import CHALLENGED, WORKING, format_bisect

    report = format_bisect([
        _stage("plain search", WORKING, 50),
        _stage("+ sold filter", WORKING, 48),
        _stage("+ category", WORKING, 47),
        _stage("+ sort by ended", WORKING, 47),
        _stage("+ 60 per page", WORKING, 60),
        _stage("+ 240 per page", CHALLENGED),
        _stage("+ price band", CHALLENGED),
    ])

    assert "Last one that worked:  + 60 per page" in report
    assert "First one refused:     + 240 per page" in report
    assert "items_per_page: 60" in report          # the actionable fix


def test_bisect_says_so_when_nothing_fails():
    from nflcarddb.diagnose import WORKING, format_bisect

    report = format_bisect([_stage("plain search", WORKING, 50),
                            _stage("+ price band", WORKING, 47)])
    assert "Every rung worked" in report


def test_bisect_distinguishes_a_blanket_refusal():
    """If even a plain search fails the query string is not the problem, and
    pointing at a parameter would send you down the wrong path."""
    from nflcarddb.diagnose import REFUSED, format_bisect

    report = format_bisect([_stage("plain search", REFUSED),
                            _stage("+ sold filter", REFUSED)])
    assert "not about the query" in report
    assert "First one refused" not in report


def test_bisect_handles_no_browser():
    from nflcarddb.diagnose import format_bisect

    assert "Could not start a browser" in format_bisect([])


def test_bisect_does_not_blame_a_rung_that_later_rungs_contradict():
    """Peyton's real report: the cold first navigation was challenged and every
    harder URL after it worked, including the collector's own query. Reading
    that as "the plain search is the trigger" is nonsense, and it was."""
    from nflcarddb.diagnose import CHALLENGED, WORKING, format_bisect

    report = format_bisect([
        _stage("plain search", CHALLENGED),
        _stage("+ sold filter", WORKING, 60),
        _stage("+ category", WORKING, 60),
        _stage("+ sort by ended", WORKING, 54),
        _stage("+ 60 per page", WORKING, 111),
        _stage("+ 240 per page", WORKING, 236),
        _stage("+ price band", WORKING, 240),
    ])

    assert "transient" in report
    assert "So the trigger is what" not in report
    assert "retries after a" in report


def test_bisect_still_blames_a_rung_when_nothing_after_it_works():
    from nflcarddb.diagnose import CHALLENGED, WORKING, format_bisect

    report = format_bisect([
        _stage("plain search", WORKING, 50),
        _stage("+ sold filter", WORKING, 48),
        _stage("+ category", CHALLENGED),
        _stage("+ sort by ended", CHALLENGED),
    ])
    assert "First one refused:     + category" in report
    assert "transient" not in report
