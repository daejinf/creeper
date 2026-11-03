"""Calendar facts are computed by code rather than guessed by the model."""
import re
from datetime import date, timedelta

DAYS='월화수목금토일'


def calendar_context(reference, text='', end=None):
    dates={reference}
    if end:
        dates.update(reference+timedelta(days=i) for i in range(min(366,(end-reference).days+1)))
    for match in re.finditer(r'(?<!\d)(?:(20\d\d)[./-])?(\d{1,2})[./-](\d{1,2})(?!\d)',text):
        try:dates.add(date(int(match[1] or reference.year),int(match[2]),int(match[3])))
        except ValueError:pass
    return {d.isoformat():DAYS[d.weekday()]+'요일' for d in sorted(dates)}


def weekday_errors(value, reference, path='group_plan'):
    if isinstance(value,dict):
        for key,item in value.items():yield from weekday_errors(item,reference,path+'.'+key)
    elif isinstance(value,list):
        for i,item in enumerate(value):yield from weekday_errors(item,reference,path+'.'+str(i))
    elif isinstance(value,str):
        for match in re.finditer(r'(?<!\d)(?:(20\d\d)[./-])?(\d{1,2})[./-](\d{1,2})\s*\(?([월화수목금토일])요일',value):
            if re.match(r'\s*아님',value[match.end():]):continue
            try:day=date(int(match[1] or reference.year),int(match[2]),int(match[3]))
            except ValueError:continue
            if DAYS[day.weekday()]!=match[4]:yield path,f'{day.isoformat()}는 {DAYS[day.weekday()]}요일입니다. 날짜와 요일을 일치시키세요.'
