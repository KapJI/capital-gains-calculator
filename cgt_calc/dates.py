import datetime

internal_start_date = datetime.date(2010, 1, 1)

# Number of days from some unspecified fixed date
DateIndex = int


def is_date(date: datetime.date) -> bool:
    if not isinstance(date, datetime.date) or isinstance(date, datetime.datetime):
        raise Exception(f'should be datetime.date: {type(date)} "{date}"')
    return True


def date_to_index(date: datetime.date) -> DateIndex:
    assert is_date(date)
    return (date - internal_start_date).days


def date_from_index(date_index: int) -> datetime.date:
    return internal_start_date + datetime.timedelta(days=date_index)
