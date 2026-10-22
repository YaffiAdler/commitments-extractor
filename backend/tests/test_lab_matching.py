from datetime import date

from app.lab_system.base import ReceivedSample
from app.lab_system.matching import CommitmentWithoutSample, match_samples, query_range
from app.schemas import Commitment, Field

# Dummy values only.
DAY = date(2025, 1, 1)
OTHER_DAY = date(2025, 1, 2)


def _commitment(*centrals: str | None, received: date | None = DAY) -> Commitment:
    return Commitment(central_numbers=[Field[str](value=c) for c in centrals],
                      received_date=Field[date](value=received))


def _sample(central: str, received: date = DAY, sender: str = "כללית") -> ReceivedSample:
    return ReceivedSample(central_number=central, received_date=received, sender=sender)


def test_splits_into_three_groups():
    commitments = {"a": _commitment("111111"), "b": _commitment("222222")}
    samples = [_sample("111111"), _sample("333333")]
    result = match_samples(commitments, samples)
    assert [(m.commitment_id, m.central_number) for m in result.matched] == [("a", "111111")]
    assert result.matched[0].date_differs is False
    assert [s.central_number for s in result.samples_without_commitment] == ["333333"]
    assert result.commitments_without_sample == [CommitmentWithoutSample(commitment_id="b", central_number="222222")]


def test_exact_string_only_leading_zeros_matter():
    result = match_samples({"a": _commitment("0234567")}, [_sample("234567")])
    assert result.matched == []
    assert len(result.samples_without_commitment) == 1 and len(result.commitments_without_sample) == 1


def test_different_date_still_matches_and_is_marked_without_changing_fields():
    commitment = _commitment("345678")
    before = commitment.model_dump()
    result = match_samples({"a": commitment}, [_sample("345678", OTHER_DAY)])
    assert result.matched[0].date_differs is True
    assert result.matched[0].commitment_received_date == DAY
    assert commitment.model_dump() == before


def test_unread_central_or_date_is_not_guessed():
    commitments = {"none": _commitment(), "unread": _commitment(None), "nodate": _commitment("111111", received=None)}
    result = match_samples(commitments, [_sample("111111")])
    assert [c.commitment_id for c in result.commitments_without_sample] == ["none", "unread"]
    assert all(c.central_number is None for c in result.commitments_without_sample)
    assert result.matched[0].date_differs is None


def test_several_centrals_on_one_form_each_checked():
    result = match_samples({"a": _commitment("111111", "222222", "111111")}, [_sample("111111")])
    assert [m.central_number for m in result.matched] == ["111111"]  # the identical repeat once
    assert result.commitments_without_sample == [CommitmentWithoutSample(commitment_id="a", central_number="222222")]


def test_query_range_widens_read_dates_or_stays_open():
    assert query_range([_commitment(received=DAY), _commitment(received=OTHER_DAY)], 1) == (
        date(2024, 12, 31), date(2025, 1, 3))
    assert query_range([_commitment(received=None)], 7) == (None, None)
