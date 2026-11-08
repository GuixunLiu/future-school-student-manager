"""Configurable civil deadlines in the institution's Asia/Shanghai time zone.

Persist the calculated deadline with each period. Editing defaults must not
silently change historical deadlines or turn previous late work into on-time work.
"""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')


@dataclass(frozen=True)
class Rule:
    frequency: str
    day: int | str
    hour: int
    minute: int = 0

    def __post_init__(self):
        if self.frequency not in {'weekly', 'monthly'}:
            raise ValueError('Unknown reporting frequency')
        if type(self.hour) is not int or not 0 <= self.hour <= 23:
            raise ValueError('Invalid hour')
        if type(self.minute) is not int or not 0 <= self.minute <= 59:
            raise ValueError('Invalid minute')
        if self.frequency == 'weekly':
            if type(self.day) is not int or not 0 <= self.day <= 6:
                raise ValueError('Weekly day must be Monday=0 through Sunday=6')
        elif self.day != 'last' and (type(self.day) is not int or not 1 <= self.day <= 31):
            raise ValueError('Monthly day must be 1..31 or last')


DEFAULTS = {
    'weekly_plan': Rule('weekly', 1, 9),
    'weekly_report': Rule('weekly', 6, 18),
    'monthly_plan': Rule('monthly', 1, 9),
    'monthly_report': Rule('monthly', 'last', 18),
}


def deadline(period_start: date, rule: Rule) -> int:
    if type(period_start) is not date:
        raise ValueError('Use an explicit reporting period date')
    if rule.frequency == 'weekly':
        if period_start.weekday() != 0:
            raise ValueError('Weekly periods must begin on Monday')
        target = period_start + timedelta(days=rule.day)
    else:
        if period_start.day != 1:
            raise ValueError('Monthly periods must begin on day 1')
        last = monthrange(period_start.year, period_start.month)[1]
        # Short months use their last real day rather than rolling into next month.
        day = last if rule.day == 'last' else min(rule.day, last)
        target = period_start.replace(day=day)
    return int(datetime.combine(target, time(rule.hour, rule.minute), TZ).timestamp())


def submission_status(*, now: int, due: int, submitted_at: int | None):
    if submitted_at is not None:
        return 'submitted' if submitted_at < due else 'late'
    return 'overdue' if now >= due else 'upcoming'


def may_edit(*, now: int, due: int, unlocked_until: int | None = None):
    return now < due or (unlocked_until is not None and now < unlocked_until)
