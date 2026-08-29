"""The small parsers that decide what a district is and what a margin means.

Each of these has exactly one wrong answer that looks plausible, which is why
they are worth pinning: a silently-zero lean reads downstream as a perfectly
balanced district, and an uncontested race scored as 0.0 puts a fake margin
into the variance the backtest measures.
"""
import math

import pytest

import fetch_house_backtest_data as fetch
import house_ingest


class TestDistrictPadding:
    @pytest.mark.parametrize("raw,expected", [
        (1, "01"), ("1", "01"), (1.0, "01"), ("01", "01"),
        (7, "07"), (10, "10"), ("10", "10"), (53, "53"),
    ])
    def test_house_ingest_pad(self, raw, expected):
        assert house_ingest._pad(raw) == expected

    @pytest.mark.parametrize("raw", ["00", "-1", 0, -1])
    def test_at_large_normalizes_to_01(self, raw):
        """At-large is '00' at the FEC and '-1' at 538; both mean the state's
        single seat, which the rest of the repo calls '01'."""
        assert fetch._pad(raw) == "01"

    def test_padding_sorts_correctly_as_text(self):
        """The reason for zero-padding at all: districts are stored as TEXT,
        and '10' < '7' character-by-character."""
        assert sorted(house_ingest._pad(n) for n in (1, 7, 10, 2)) == ["01", "02", "07", "10"]


class TestLeanParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("R+15.21", -15.21),   # 2018/2020 vintages publish text
        ("D+3.5", 3.5),
        ("-15.21", -15.21),    # 2021/2022 vintages publish signed floats
        ("3.5", 3.5),
        ("0", 0.0),
    ])
    def test_both_published_formats(self, raw, expected):
        assert fetch.parse_lean_value(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_blank_is_none_not_zero(self, raw):
        assert fetch.parse_lean_value(raw) is None

    @pytest.mark.parametrize("raw", ["EVEN", "R+", "not a lean", "D+x"])
    def test_unparseable_raises_rather_than_returning_zero(self, raw):
        """A silently-zero lean is the one wrong answer that looks plausible:
        downstream it reads as a perfectly balanced district."""
        with pytest.raises(ValueError):
            fetch.parse_lean_value(raw)


class TestTwoPartyMargin:
    def test_sign_convention_is_d_positive(self):
        assert fetch.two_party_margin(60, 40) == pytest.approx(20.0)
        assert fetch.two_party_margin(40, 60) == pytest.approx(-20.0)

    def test_tied_race_is_zero(self):
        assert fetch.two_party_margin(50, 50) == pytest.approx(0.0)

    @pytest.mark.parametrize("dem,rep", [(100, 0), (0, 100), (0, 0)])
    def test_uncontested_is_none_not_a_margin(self, dem, rep):
        """An uncontested seat has no two-party margin. Returning +/-100 would
        put a fake blowout into every variance the backtest computes."""
        assert fetch.two_party_margin(dem, rep) is None

    def test_margin_closes_with_the_share_convention(self):
        """The model turns a margin back into shares with
        dem_share = 50 + margin/2, which only closes if the two shares sum to
        100 — i.e. only on the TWO-party scale, not the all-party one."""
        margin = fetch.two_party_margin(55, 45)
        dem_share = 50 + margin / 2
        rep_share = 50 - margin / 2
        assert dem_share + rep_share == pytest.approx(100.0)
        assert dem_share == pytest.approx(55.0)
